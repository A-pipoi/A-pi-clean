import { readFile, writeFile } from 'node:fs/promises';
const file=new URL('../dist/paper-page.mjs',import.meta.url);
let code=await readFile(file,'utf8');
const begin=code.indexOf('  const orders = paper.orders || [];');
const end=code.indexOf('  services.applyRiskSnapshot(',begin);
if(begin<0||end<0)throw Error('Order rendering target missing');
const block=code.slice(begin,end).replace('  const orders = paper.orders || [];\n','');
code=code.slice(0,begin)+'  renderOrderRows(orderRows ?? (paper.orders || []).slice(0,20));\n'+code.slice(end);
code+=`\n/** @type {NonNullable<import('../types/domain').PaperState['orders']> | null} */
let orderRows = null;
let orderPage = 1, orderPages = 1, orderTotal = 0, orderLoading = false;
/** @type {HTMLElement|null} */
let orderPager = null;
/** @param {NonNullable<import('../types/domain').PaperState['orders']>} orders */
function renderOrderRows(orders) {
  const body = requiredElement("paperOrdersBody");
  if (!body) return;
${block}}
async function loadOrderPage() {
  if(orderLoading || !services) return;
  orderLoading = true;
  renderOrderPager();
  try {
    const response = await fetch('/api/paper/orders?page='+orderPage+'&page_size=20', { cache:'no-store', signal:AbortSignal.timeout(15000) });
    const data=await response.json();
    if(!response.ok || !data.ok || !Array.isArray(data.orders))throw Error(data.error || '历史委托分页不可用');
    orderRows=data.orders.slice(0,20); orderPage=data.page; orderPages=data.pages; orderTotal=data.total;
    renderOrderRows(orderRows || []);
  } catch(caught) { services.showToast('历史委托加载失败 · '+toError(caught).message); }
  finally { orderLoading=false; renderOrderPager(); }
}
function renderOrderPager() {
  if(!orderPager) {
    orderPager=document.createElement('nav');orderPager.className='order-pagination';orderPager.setAttribute('aria-label','纸面委托分页');
    requiredElement('paperOrdersBody')?.closest('table')?.after(orderPager);
  }
  orderPager.replaceChildren();
  const label=document.createElement('span');label.textContent=orderLoading?'正在读取委托…':('第 '+orderPage+' / '+orderPages+' 页 · 共 '+orderTotal+' 条');
  const previous=document.createElement('button'),next=document.createElement('button');
  previous.type=next.type='button'; previous.textContent='上一页';next.textContent='下一页';
  previous.disabled=orderLoading || orderPage<=1;next.disabled=orderLoading || orderPage>=orderPages;
  previous.addEventListener('click',()=>{orderPage--;void loadOrderPage();});
  next.addEventListener('click',()=>{orderPage++;void loadOrderPage();});
  orderPager.append(label,previous,next);
}
`;
code=code.replace('    applyPaperState(paperState, data.scheduler || null, data.risk_limits);','    applyPaperState(paperState, data.scheduler || null, data.risk_limits);\n    await loadOrderPage();');
await writeFile(file,code);
