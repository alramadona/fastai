"`Learner` support for computer vision"
from ..torch_core import *
from ..basic_train import *
from ..data import *
from ..layers import *
import torchvision.models as tvm

__all__ = ['ConvLearner', 'create_body', 'create_head', 'num_features', 'ClassificationInterpretation']

def create_body(model:Model, cut:Optional[int]=None, body_fn:Callable[[Model],Model]=None):
    "Cut off the body of a typically pretrained `model` at `cut` or as specified by `body_fn`."
    return (nn.Sequential(*list(model.children())[:cut]) if cut
            else body_fn(model) if body_fn else model)

def num_features(m:Model)->int:
    "Return the number of output features for a `model`."
    for l in reversed(flatten_model(m)):
        if hasattr(l, 'num_features'): return l.num_features

def create_head(nf:int, nc:int, lin_ftrs:Optional[Collection[int]]=None, ps:Floats=0.5):
    """Model head that takes `nf` features, runs through `lin_ftrs`, and about `nc` classes.
    :param ps: dropout, can be a single float or a list for each layer."""
    lin_ftrs = [nf, 512, nc] if lin_ftrs is None else [nf] + lin_ftrs + [nc]
    ps = listify(ps)
    if len(ps)==1: ps = [ps[0]/2] * (len(lin_ftrs)-2) + ps
    actns = [nn.ReLU(inplace=True)] * (len(lin_ftrs)-2) + [None]
    layers = [AdaptiveConcatPool2d(), Flatten()]
    for ni,no,p,actn in zip(lin_ftrs[:-1],lin_ftrs[1:],ps,actns):
        layers += bn_drop_lin(ni,no,True,p,actn)
    return nn.Sequential(*layers)

# By default split models between first and second layer
def _default_split(m:Model): return (m[1],)
# Split a resnet style model
def _resnet_split(m:Model): return (m[0][6],m[1])

_default_meta = {'cut':-1, 'split':_default_split}
_resnet_meta  = {'cut':-2, 'split':_resnet_split }

model_meta = {
    tvm.resnet18 :{**_resnet_meta}, tvm.resnet34: {**_resnet_meta},
    tvm.resnet50 :{**_resnet_meta}, tvm.resnet101:{**_resnet_meta},
    tvm.resnet152:{**_resnet_meta}}

class ConvLearner(Learner):
    "Build convnet style learners."
    def __init__(self, data:DataBunch, arch:Callable, cut:Union[int,Callable]=None, pretrained:bool=True,
                 lin_ftrs:Optional[Collection[int]]=None, ps:Floats=0.5,
                 custom_head:Optional[nn.Module]=None, split_on:Optional[SplitFuncOrIdxList]=None, **kwargs:Any)->None:
        meta = model_meta.get(arch, _default_meta)
        torch.backends.cudnn.benchmark = True
        body = create_body(arch(pretrained), ifnone(cut,meta['cut']))
        nf = num_features(body) * 2
        head = custom_head or create_head(nf, data.c, lin_ftrs, ps)
        model = nn.Sequential(body, head)
        super().__init__(data, model, **kwargs)
        self.split(ifnone(split_on,meta['split']))
        if pretrained: self.freeze()
        apply_init(model[1], nn.init.kaiming_normal_)

class ClassificationInterpretation():
    "Interpretation methods for classification models"
    def __init__(self, data:DataBunch, y_pred:Tensor, y_true:Tensor,
                 loss_class:type=nn.CrossEntropyLoss, sigmoid:bool=True):
        self.data,self.y_pred,self.y_true,self.loss_class = data,y_pred,y_true,loss_class
        self.losses = calc_loss(y_pred, y_true, loss_class=loss_class)
        self.probs = y_pred.sigmoid() if sigmoid else y_pred
        self.pred_class = self.probs.argmax(dim=1)

    @classmethod
    def from_learner(cls, learn:Learner, loss_class:type=nn.CrossEntropyLoss, sigmoid:bool=True):
        "Factory method to create from a Learner"
        return cls(learn.data, *learn.get_preds(), loss_class=loss_class, sigmoid=sigmoid)

    def top_losses(self, k, largest=True):
        "`k` largest(/smallest) losses"
        return self.losses.topk(k, largest=largest)

    def plot_top_losses(self, k, largest=True, figsize=(12,12)):
        "Show images in `top_losses` along with their loss, label, and prediction"
        tl = self.top_losses(k,largest)
        classes = self.data.classes
        rows = math.ceil(math.sqrt(k))
        fig,axes = plt.subplots(rows,rows,figsize=figsize)
        for i,idx in enumerate(self.top_losses(k, largest=largest)[1]):
            t=self.data.valid_ds[idx]
            t[0].show(ax=axes.flat[i], title=
                f'{classes[self.pred_class[idx]]}/{classes[t[1]]} / {self.losses[idx]:.2f} / {self.probs[idx][t[1]]:.2f}')

    def confusion_matrix(self):
        "Confusion matrix as an `np.ndarray`"
        x=torch.arange(0,self.data.c)
        cm = ((self.pred_class==x[:,None]) & (self.y_true==x[:,None,None])).sum(2)
        return to_np(cm)

    def plot_confusion_matrix(self, normalize:bool=False, title:str='Confusion matrix', cmap:Any="Blues", figsize:tuple=None):
        "Plot the confusion matrix"
        # This function is copied from the scikit docs
        cm = self.confusion_matrix()
        plt.figure(figsize=figsize)
        plt.imshow(cm, interpolation='nearest', cmap=cmap)
        plt.title(title)
        plt.colorbar()
        tick_marks = np.arange(len(self.data.classes))
        plt.xticks(tick_marks, self.data.classes, rotation=45)
        plt.yticks(tick_marks, self.data.classes)

        if normalize: cm = cm.astype('float') / cm.sum(axis=1)[:, np.newaxis]
        thresh = cm.max() / 2.
        for i, j in itertools.product(range(cm.shape[0]), range(cm.shape[1])):
            plt.text(j, i, cm[i, j], horizontalalignment="center", color="white" if cm[i, j] > thresh else "black")

        plt.tight_layout()
        plt.ylabel('True label')
        plt.xlabel('Predicted label')

