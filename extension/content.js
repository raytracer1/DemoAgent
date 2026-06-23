chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.action === 'getUrl') { sendResponse(window.location.href); }
  else if (msg.action === 'extractElements') { sendResponse(extractElements()); }
  else if (msg.action === 'execute') {
    executeStep(msg.step).then(r => sendResponse(r)).catch(e => sendResponse({ error: e.message }));
    return true;
  }
});

function extractElements() {
  const results = [], seen = new Set();
  let id = 1;
  (function walk(node, depth) {
    if (!node || depth > 20) return;
    if (node.nodeType === 1) {
      const tag = (node.tagName||'').toLowerCase();
      const rect = node.getBoundingClientRect();
      const style = getComputedStyle(node);
      const vis = rect.width>0 && rect.height>0 && style.display!=='none' && style.visibility!=='hidden' && parseFloat(style.opacity)>0;
      if (vis && (tag==='button'||tag==='a'||tag==='input'||tag==='select'||tag==='textarea'||node.getAttribute('role')==='button'||node.getAttribute('role')==='link'||node.getAttribute('role')==='textbox'||node.getAttribute('role')==='combobox'||node.getAttribute('role')==='option'||node.getAttribute('contenteditable')==='true')) {
        const aria=node.getAttribute('aria-label')||'', place=node.getAttribute('placeholder')||'';
        let txt=(node.textContent||'').trim().slice(0,60);
        if (tag==='input'||tag==='textarea') txt=(node.getAttribute('type')||'text')+' input'+(place?' "'+place+'"':'');
        if (tag==='a'){const h=node.getAttribute('href')||''; if(h&&!txt)txt=h;}
        const lbl=aria||place||txt;
        if(lbl&&!seen.has(lbl+tag)){seen.add(lbl+tag); results.push({id:id++,tag,text:lbl.slice(0,100)});}
      }
    }
    for(const c of node.childNodes)walk(c,depth+1);
  })(document.body,0);
  return results;
}

async function executeStep(step) {
  const action = (step.action||'click').toLowerCase();
  const text = step.text||'';
  const value = step.value||'';
  const searchText = text.slice(0,40).toLowerCase();

  // Find by text match
  const all = document.querySelectorAll('button,a,input,select,textarea,[role="button"],[role="link"],[role="textbox"],[role="combobox"],[role="option"]');
  let target = null;
  for (const n of all) {
    const nt = ((n.textContent||'')+(n.getAttribute('aria-label')||'')+(n.getAttribute('placeholder')||'')).toLowerCase();
    if (nt.includes(searchText)) { target = n; break; }
  }
  if (!target) {
    const w = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT);
    let n;
    while (n = w.nextNode()) {
      const tn = (n.textContent||'').trim().slice(0,40).toLowerCase();
      if (tn === searchText) { target = n.closest('button,a,input,select,textarea')||n; break; }
    }
  }
  if (!target) return { ok: false, error: 'Not found: '+text };

  target.scrollIntoView({block:'center'});
  await new Promise(r=>setTimeout(r,300));

  if (action==='click') target.click();
  else if (action==='type') { target.focus(); target.value=value; target.dispatchEvent(new Event('input',{bubbles:true})); }
  else if (action==='select') { target.value=value; target.dispatchEvent(new Event('change',{bubbles:true})); }
  await new Promise(r=>setTimeout(r,1000));
  return { ok: true };
}
