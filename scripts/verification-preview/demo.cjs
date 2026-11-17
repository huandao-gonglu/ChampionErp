// 两个测试页面均由独立内存分区本地响应，保留跨域 iframe 的实际输入行为。
const offer = 'https://detail.1688.com/offer/900001.html'
const challenge = 'https://slide.1688.com/verify'
const product = `<!doctype html><meta charset="utf-8"><style>
body{background:#eff3ef;color:#203c32;font:18px system-ui;margin:40px}
dialog{position:fixed;inset:0;margin:auto;border:0;border-radius:14px;padding:0;box-shadow:0 12px 64px #152c2840}
iframe{display:block;border:0;width:390px;height:210px}dialog::backdrop{background:#102b2440}</style>
<h1>本地示例商品</h1><p>这是用来检查裁剪范围的完整商品页。</p>
<script type="application/json" id="buyer">{"buyerModel":{"userId":null}}</script>
<dialog><iframe title="模拟滑块" src="${challenge}"></iframe></dialog>
<script>
const dlg=document.querySelector('dialog');dlg.showModal();
addEventListener('message', e=>{
 if(e.origin!=='https://slide.1688.com'||e.source!==dlg.querySelector('iframe').contentWindow)return;
 if(e.data==='expand')dlg.querySelector('iframe').style.height='295px';
 if(e.data==='done'){dlg.remove();document.querySelector('#buyer').textContent=JSON.stringify({buyerModel:{userId:42}})}
});
</script>`
const frame = `<!doctype html><meta charset="utf-8"><style>
*{box-sizing:border-box}body{padding:21px 24px;margin:0;color:#18352e;background:white;font:15px system-ui}
h2{font-size:22px;margin:0 0 7px}p{margin:0 0 13px;color:#49655a}
input{display:block;width:100%;height:34px;accent-color:#116b54}button{background:none;border:none;color:#126555;padding:7px 0;font:inherit;text-decoration:underline}
</style><h2>滑块验证示例</h2><p>将滑块拖到最右侧后松开。</p>
<input type="range" min="0" max="100" value="0" aria-label="验证滑块">
<button>展开说明</button><p hidden>弹窗会变高。你可以切到完整页面，再切回局部继续拖动。</p>
<script>
const target='https://detail.1688.com';const slider=document.querySelector('input');
slider.onchange=()=>{if(slider.value==='100')parent.postMessage('done',target)};
document.querySelector('button').onclick=e=>{e.target.hidden=true;document.querySelector('p[hidden]').hidden=false;parent.postMessage('expand',target)};
</script>`
function installDemo(store) {
  store.protocol.handle('https', req => {
    const body = req.url === offer ? product : req.url === challenge ? frame : undefined
    return new Response(body, { status: body ? 200 : 404, headers: { 'content-type': 'text/html; charset=utf-8' } })
  })
}
module.exports = { offer, installDemo }
