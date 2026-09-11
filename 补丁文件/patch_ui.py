"""Targeted presentation patches; normal navigation and transaction methods stay intact."""
from pathlib import Path
import re, json, hashlib

BASE = Path(__file__).resolve().parent.parent / '.analysis' / 'noads'
TREE = BASE / 'decoded-v2'
BACKUP = BASE / 'ui-backup'
pending = {}
changes = []

def file_path(cls, dex=5):
    return TREE / f'smali_classes{dex}' / (cls + '.smali')

def read(cls, dex=5):
    path = file_path(cls, dex)
    if path not in pending:
        backup = BACKUP / path.relative_to(TREE)
        if not backup.exists():
            backup.parent.mkdir(parents=True, exist_ok=True)
            backup.write_bytes(path.read_bytes())
        pending[path] = backup.read_text(encoding='utf-8')
    return path, pending[path]

def change(cls, sig, body=None, transform=None, dex=5):
    path, content = read(cls, dex)
    pattern = re.compile(r'^\.method [^\n]* ' + re.escape(sig) + r'\n.*?^\.end method', re.M | re.S)
    matches = list(pattern.finditer(content))
    assert len(matches) == 1, (cls, sig, len(matches))
    m = matches[0]
    old = m.group()
    new = transform(old) if transform else old.splitlines()[0] + '\n' + body.strip('\n') + '\n.end method'
    pending[path] = content[:m.start()] + new + content[m.end():]
    changes.append({'file':str(path.relative_to(TREE)), 'method':sig,
        'before_sha256':hashlib.sha256(old.encode()).hexdigest(), 'after_sha256':hashlib.sha256(new.encode()).hexdigest()})

def no_op(cls, sig, dex=5):
    change(cls, sig, '    .locals 0\n    return-void', dex=dex)

def hide(reg):
    return f'    invoke-static {{{reg}}}, Lcom/qiekj/user/ad/NoAdsUi;->hide(Landroid/view/View;)V'

def hide_only(cls,sig,reg):
    change(cls,sig,'    .locals 0\n'+hide(reg)+'\n    return-void')

adext='com/qiekj/user/ad/AdExtKt'
for sig,reg in [
 ('loadTopOnSlot(Landroidx/appcompat/app/AppCompatActivity;Landroid/view/ViewGroup;Lcom/qiekj/user/entity/home/AdBean;)V','p1'),
 ('loadFixedAd(Landroid/app/Activity;Lcom/qiekj/user/entity/home/AdBean;Landroid/widget/ImageView;)V','p2'),
 ('loadImageUrlAd(Landroid/content/Context;Lcom/qiekj/user/entity/home/AdBean;Landroid/view/ViewGroup;)V','p2'),
 ('initBannerAd(Landroid/app/Activity;Lcom/youth/banner/Banner;Z)V','p1')]:
    hide_only(adext,sig,reg)
for sig in [
 'loadTopOnDialog(Landroidx/appcompat/app/AppCompatActivity;Lcom/qiekj/user/entity/home/AdBean;)V',
 'loadBannerDialog(Landroidx/appcompat/app/AppCompatActivity;Lcom/qiekj/user/entity/home/AdBean;)V']:
    no_op(adext,sig)
change(adext,'floatAd(Landroid/app/Activity;Lcom/qiekj/user/entity/home/AdBean;Landroid/view/ViewGroup;Landroid/widget/ImageView;)V',
    '    .locals 0\n'+hide('p2')+'\n'+hide('p3')+'\n    return-void')
change(adext,'showImgDialog(Landroid/content/Context;Lcom/qiekj/user/entity/home/ImageBean;Lkotlin/jvm/functions/Function0;Lkotlin/jvm/functions/Function0;)V',
    '''    .locals 0
    invoke-static {p3}, Lcom/qiekj/user/ad/NoAdsUi;->closeLater(Lkotlin/jvm/functions/Function0;)V
    return-void''')

change('com/qiekj/user/ad/FloatAdManager','showAd(Lcom/qiekj/user/entity/home/AdBean;)V',
    '''    .locals 1
    invoke-direct {p0}, Lcom/qiekj/user/ad/FloatAdManager;->reset()V
    sget-object v0, Lcom/qiekj/user/ad/FloatingAdState;->EMPTY:Lcom/qiekj/user/ad/FloatingAdState;
    iput-object v0, p0, Lcom/qiekj/user/ad/FloatAdManager;->state:Lcom/qiekj/user/ad/FloatingAdState;
    invoke-direct {p0}, Lcom/qiekj/user/ad/FloatAdManager;->hideView()V
    return-void''')

for sig in [
 'load(Landroid/app/Activity;Landroid/view/ViewGroup;Ljava/lang/String;Lkotlin/jvm/functions/Function1;Lkotlin/jvm/functions/Function1;Lkotlin/jvm/functions/Function1;)V',
 'showBackup(Landroid/app/Activity;Landroid/view/ViewGroup;)V']:
    hide_only('com/qiekj/user/ad/EcMallFeedAd',sig,'p2')

for cls, sigs in {
 'com/qiekj/user/ad/HotSplashAd':['loadAndShow(Landroid/app/Activity;Ljava/lang/String;)V','showAd(Landroid/app/Activity;)V'],
 'com/qiekj/user/ad/gromore/HotSplash':['loadSplash(Landroid/app/Activity;)V','showSplash(Landroid/app/Activity;)V'],
 'com/qiekj/user/ad/gromore/HotSplashV2':['loadAndShow(Landroid/app/Activity;)V','showSplash(Landroid/app/Activity;)V'],
}.items():
    for sig in sigs:no_op(cls,sig)
change('com/qiekj/user/ad/gromore/HotSplashAct','loadAndShow()V',
    '    .locals 0\n    invoke-virtual {p0}, Landroid/app/Activity;->finish()V\n    return-void')

def foreground(old):
    # Preserve application lifecycle timekeeping and existing non-ad callbacks.
    marker='    :cond_0\n'
    assert old.count(marker)==1
    return old.split(marker)[0]+marker+'    return-void\n.end method'
change('com/qiekj/App','onApplicationForeground(Landroid/app/Activity;)V',transform=foreground,dex=4)

change('com/qiekj/user/ui/activity/SplashDialogFragment','initView()V',
    '''    .locals 4
    iget-object v0, p0, Lcom/qiekj/user/ui/activity/SplashDialogFragment;->mHandler:Landroid/os/Handler;
    new-instance v1, Lcom/qiekj/user/ui/activity/SplashDialogFragment$$ExternalSyntheticLambda5;
    invoke-direct {v1, p0}, Lcom/qiekj/user/ui/activity/SplashDialogFragment$$ExternalSyntheticLambda5;-><init>(Lcom/qiekj/user/ui/activity/SplashDialogFragment;)V
    const-wide/16 v2, 0x0
    invoke-virtual {v0, v1, v2, v3}, Landroid/os/Handler;->postDelayed(Ljava/lang/Runnable;J)Z
    return-void''')

for name in ['DeviceStartupAct','PayStateAct']:
    no_op('com/qiekj/user/ui/activity/scan/'+name,'getVideoAd(Ljava/lang/String;Ljava/lang/String;)V')
for sig in ['loadAdxUsingCard()V','showAdxUsingCard(Ljava/util/List;)V']:
    no_op('com/qiekj/user/ui/activity/scan/DeviceStartupAct',sig)
for name in ['OrderPayPreviewAct','AfterPayUseAct']:
    for sig in ['loadPackageAd()V','loadPackageRunningAd()V',
      'showCashierAdxTask(Lcom/qiekj/user/entity/IntegralTaskItemBean;Lcom/qiekj/user/entity/ThirdTaskLink;)V']:
        no_op('com/qiekj/user/ui/activity/scan/'+name,sig)

change('com/qiekj/user/ui/activity/scan/adx/ScanAdxGate',
 'tryShow(Landroidx/appcompat/app/AppCompatActivity;Ljava/lang/String;Lkotlin/coroutines/Continuation;)Ljava/lang/Object;',
 '''    .locals 1
    sget-object v0, Ljava/lang/Boolean;->FALSE:Ljava/lang/Boolean;
    return-object v0''')

for name,method in [('HomeFragment','initData$lambda$11'),('HomeFragmentV1','initData$lambda$43'),
                    ('HomeFragmentV2','initData$lambda$45'),('HomeFragmentV3','initData$lambda$47')]:
    cls='com/qiekj/user/ui/fragment/'+name
    change(cls,method+'(L'+cls+';Lcom/qiekj/user/entity/home/AdBean;)Lkotlin/Unit;',
           '    .locals 1\n    sget-object v0, Lkotlin/Unit;->INSTANCE:Lkotlin/Unit;\n    return-object v0')

def adapter_guard(old):
    assert '    .locals 11\n' in old
    code='''
    # Ad-only recycler rows; normal product row types keep their original binding.
    invoke-interface {p2}, Lcom/chad/library/adapter/base/entity/MultiItemEntity;->getItemType()I
    move-result v0
    if-eqz v0, :noads_row
    const/4 v1, 0x1
    if-ne v0, v1, :noads_original
    :noads_row
    iget-object v0, p1, Landroidx/recyclerview/widget/RecyclerView$ViewHolder;->itemView:Landroid/view/View;
    invoke-static {v0}, Lcom/qiekj/user/ad/NoAdsUi;->collapseRow(Landroid/view/View;)V
    return-void
    :noads_original
'''
    return old.replace('    .locals 11\n','    .locals 11\n'+code,1)
change('com/qiekj/user/adapter/HomeMallContentAdapter',
 'convert(Lcom/chad/library/adapter/base/viewholder/BaseViewHolder;Lcom/chad/library/adapter/base/entity/MultiItemEntity;)V',transform=adapter_guard)

helpers={
 'com/qiekj/user/ad/NoAdsUi': '''.class public final Lcom/qiekj/user/ad/NoAdsUi;
.super Ljava/lang/Object;

.method public static hide(Landroid/view/View;)V
    .locals 1
    if-eqz p0, :done
    const/16 v0, 0x8
    invoke-virtual {p0, v0}, Landroid/view/View;->setVisibility(I)V
    :done
    return-void
.end method

.method public static collapseRow(Landroid/view/View;)V
    .locals 2
    if-eqz p0, :done
    invoke-static {p0}, Lcom/qiekj/user/ad/NoAdsUi;->hide(Landroid/view/View;)V
    invoke-virtual {p0}, Landroid/view/View;->getLayoutParams()Landroid/view/ViewGroup$LayoutParams;
    move-result-object v0
    if-eqz v0, :done
    const/4 v1, 0x0
    iput v1, v0, Landroid/view/ViewGroup$LayoutParams;->height:I
    invoke-virtual {p0, v0}, Landroid/view/View;->setLayoutParams(Landroid/view/ViewGroup$LayoutParams;)V
    :done
    return-void
.end method

.method public static closeLater(Lkotlin/jvm/functions/Function0;)V
    .locals 3
    if-eqz p0, :done
    invoke-static {}, Landroid/os/Looper;->getMainLooper()Landroid/os/Looper;
    move-result-object v0
    new-instance v1, Landroid/os/Handler;
    invoke-direct {v1, v0}, Landroid/os/Handler;-><init>(Landroid/os/Looper;)V
    new-instance v2, Lcom/qiekj/user/ad/NoAdsUi$Dismiss;
    invoke-direct {v2, p0}, Lcom/qiekj/user/ad/NoAdsUi$Dismiss;-><init>(Lkotlin/jvm/functions/Function0;)V
    invoke-virtual {v1, v2}, Landroid/os/Handler;->post(Ljava/lang/Runnable;)Z
    :done
    return-void
.end method
''',
 'com/qiekj/user/ad/NoAdsUi$Dismiss': '''.class public final Lcom/qiekj/user/ad/NoAdsUi$Dismiss;
.super Ljava/lang/Object;
.implements Ljava/lang/Runnable;
.field private final callback:Lkotlin/jvm/functions/Function0;

.method public constructor <init>(Lkotlin/jvm/functions/Function0;)V
    .locals 0
    invoke-direct {p0}, Ljava/lang/Object;-><init>()V
    iput-object p1, p0, Lcom/qiekj/user/ad/NoAdsUi$Dismiss;->callback:Lkotlin/jvm/functions/Function0;
    return-void
.end method

.method public run()V
    .locals 1
    iget-object v0, p0, Lcom/qiekj/user/ad/NoAdsUi$Dismiss;->callback:Lkotlin/jvm/functions/Function0;
    invoke-interface {v0}, Lkotlin/jvm/functions/Function0;->invoke()Ljava/lang/Object;
    return-void
.end method
'''
}

for path,content in pending.items():
    original = (BACKUP / path.relative_to(TREE)).read_bytes()
    if path.read_bytes() not in (original, content.encode('utf-8')):
        raise RuntimeError(f'Refuse to overwrite a class changed since backup: {path}')
for cls,content in helpers.items():
    path = file_path(cls)
    if path.exists() and path.read_bytes() != content.encode('utf-8'):
        raise RuntimeError(f'Existing helper has unexpected content: {path}')
for path,content in pending.items():path.write_text(content,encoding='utf-8',newline='\n')
for cls,content in helpers.items():file_path(cls).write_text(content,encoding='utf-8',newline='\n')
(BASE/'ui-changes.json').write_text(json.dumps({'methods':changes,'helpers':list(helpers)},ensure_ascii=False,indent=2),encoding='utf-8')
print(f'Patched {len(changes)} UI methods in {len(pending)} files; {len(helpers)} helpers added')
