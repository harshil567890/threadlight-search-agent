const $ = s => document.querySelector(s);
let current = null, busy = false;
const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
async function api(path, options = {}) {
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Request failed. Please try again.');
  return data;
}
function notice(text) { $('#notice').textContent = text; $('#notice').hidden = !text; }
function timeLabel(value) { return new Date(value).toLocaleString([], {dateStyle:'medium',timeStyle:'short'}); }
function renderMessage(role, text, timestamp, sources = []) {
  const article = document.createElement('article'); article.className = `message ${role}`;
  const meta = document.createElement('div'); meta.className = 'meta';
  const name = document.createElement('strong'); name.textContent = role === 'user' ? 'You' : '✳ Threadlight';
  const time = document.createElement('time'); time.dateTime = timestamp; time.textContent = timeLabel(timestamp);
  meta.append(name, time);
  const content = document.createElement('div'); content.className = 'content'; content.textContent = text;
  article.append(meta, content); $('#messages').append(article);
  renderContent(content, text, sources); addSources(article, sources); scrollChat(); return article;
}
function renderContent(element, text, sources) {
  element.replaceChildren(); const chars = Array.from(text); let cursor = 0;
  [...sources].sort((a,b) => a.start_index - b.start_index).forEach(source => {
    const start = source.start_index, end = source.end_index;
    if (!Number.isInteger(start) || !Number.isInteger(end) || start < cursor || end <= start || end > chars.length) return;
    try { const url = new URL(source.url); if (!['http:', 'https:'].includes(url.protocol)) return;
      element.append(document.createTextNode(chars.slice(cursor,start).join('')));
      const a = document.createElement('a'); a.href = url.href; a.target = '_blank'; a.rel = 'noopener noreferrer';
      a.textContent = chars.slice(start,end).join(''); a.title = source.title || url.hostname; element.append(a); cursor = end;
    } catch {}
  }); element.append(document.createTextNode(chars.slice(cursor).join('')));
}
function addSources(article, sources) {
  article.querySelector('.sources')?.remove();
  if (!sources.length) return;
  const div = document.createElement('div'); div.className = 'sources';
  const unique = sources.filter((source,i) => sources.findIndex(s => s.url === source.url) === i);
  unique.forEach((source, i) => {
    try { const url = new URL(source.url); if (!['http:', 'https:'].includes(url.protocol)) return;
      const a = document.createElement('a'); a.href = url.href; a.target = '_blank'; a.rel = 'noopener noreferrer';
      a.textContent = `${i + 1}. ${source.title || url.hostname}`; div.append(a);
    } catch {}
  }); article.append(div);
}
function scrollChat() { $('#messages').scrollTop = $('#messages').scrollHeight; }
async function refresh() {
  const chats = await api('/api/chats'); $('#chats').replaceChildren();
  chats.forEach(chat => { const b = document.createElement('button'); b.textContent = chat.title;
    b.title = `${chat.title} · ${timeLabel(chat.updated_at)}`; b.className = current === chat.id ? 'active' : '';
    b.disabled = busy; b.onclick = () => openChat(chat.id).catch(e => notice(e.message)); $('#chats').append(b);
    if (current === chat.id) $('#heading').textContent = chat.title;
  }); return chats;
}
async function openChat(id) {
  if (busy) return;
  const data = await api(`/api/chats/${id}`); current = id;
  $('#messages').replaceChildren(); $('#welcome').hidden = data.messages.length > 0;
  $('#delete').hidden = false;
  data.messages.forEach(m => renderMessage(m.role, m.content, m.created_at, m.sources));
  await refresh(); $('#question').focus();
}
async function newChat() {
  if (busy) return;
  const chat = await api('/api/chats', {method:'POST'}); await openChat(chat.id);
}
function setBusy(value) {
  busy = value; $('#send').disabled = value; $('#new').disabled = value; $('#delete').disabled = value;
  $('#question').disabled = value;
  document.querySelectorAll('nav button, .suggestions button').forEach(b => b.disabled = value);
}
$('#new').onclick = () => newChat().catch(e => notice(e.message));
$('#delete').onclick = async () => {
  if (!current || busy || !confirm('Delete this conversation and its memories permanently?')) return;
  try { await api(`/api/chats/${current}`, {method:'DELETE'}); current = null;
    const chats = await refresh(); if (chats.length) await openChat(chats[0].id); else await newChat();
  } catch(e) { notice(e.message); }
};
$('#export').onclick = async () => {
  try { const data = await api('/api/export'); const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)],{type:'application/json'}));
    const a = document.createElement('a'); a.href = url; a.download = 'threadlight-history.json'; a.click(); setTimeout(() => URL.revokeObjectURL(url),1000);
  } catch(e) { notice(e.message); }
};
document.querySelectorAll('[data-prompt]').forEach(b => b.onclick = () => { $('#question').value = b.dataset.prompt; $('#composer').requestSubmit(); });
$('#question').addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); $('#composer').requestSubmit(); } });
$('#question').addEventListener('input', () => { $('#question').style.height = 'auto'; $('#question').style.height = Math.min($('#question').scrollHeight,180)+'px'; });
$('#composer').onsubmit = async e => {
  e.preventDefault(); const message = $('#question').value.trim(); if (busy || !message) return;
  let reply, accepted = false, completed = false;
  try {
    if (!current) await newChat();
    setBusy(true); notice('');
    const response = await fetch('/api/chat', {method:'POST', headers:{'Content-Type':'application/json'},body:JSON.stringify({chat_id:current,message,timezone})});
    if (!response.ok) { const data = await response.json(); throw new Error(data.error); }
    accepted = true; $('#welcome').hidden = true;
    renderMessage('user',message,new Date().toISOString()); $('#question').value = ''; $('#question').style.height = 'auto';
    reply = renderMessage('assistant','',new Date().toISOString());
    const reader = response.body.getReader(), decoder = new TextDecoder(); let pending = '';
    function handle(line) {
      if (!line.trim()) return; const data = JSON.parse(line);
      if (data.type === 'status') $('#activity').textContent = `${data.message}…`;
      if (data.type === 'delta') { reply.querySelector('.content').textContent += data.text; scrollChat(); }
      if (data.type === 'error') throw new Error(data.message);
      if (data.type === 'done') { completed = true; renderContent(reply.querySelector('.content'),data.text,data.sources);
        const time = reply.querySelector('time'); time.dateTime = data.created_at; time.textContent = `${timeLabel(data.created_at)} · ${data.elapsed}s`;
        addSources(reply,data.sources); scrollChat();
      }
    }
    while (true) { const {done,value} = await reader.read(); if (done) break;
      pending += decoder.decode(value,{stream:true}); let n; while ((n = pending.indexOf('\n')) >= 0) { const line = pending.slice(0,n); pending = pending.slice(n+1); handle(line); }
    }
    pending += decoder.decode(); if (pending.trim()) handle(pending);
    if (!completed) throw new Error('Connection interrupted before the answer finished. Your question is saved.');
  } catch(error) {
    notice(error.message || 'Connection failed. Please try again.');
    if (reply && !completed) reply.remove();
    if (accepted) $('#question').value = message;
  } finally { setBusy(false); $('#activity').textContent = ''; $('#question').focus(); await refresh().catch(e => notice(e.message)); }
};
(async () => {
  try {
    const status = await api('/api/status');
    if (!status.configured) notice('Ready for setup: add OPENAI_API_KEY to .env and restart the server. Your history stays on this device’s server.');
    const chats = await refresh(); if (chats.length) await openChat(chats[0].id);
  } catch(e) { notice(e.message); }
})();
