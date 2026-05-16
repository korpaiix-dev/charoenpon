// Bot Management page — extends the existing SPA cleanly.
// Loaded AFTER app.js so it can extend NAV_ITEMS and wrap navigate().
//
// Why this works:
//   * NAV_ITEMS in app.js is `const` at script top level → accessible by
//     bare name from other scripts (NOT via window.NAV_ITEMS).
//   * navigate() is a function declaration → goes on window.
//   * renderSidebar() reads NAV_ITEMS directly each call.
//   * navigate()'s internal `pages` dict is local, so we can't extend it;
//     we wrap navigate and re-render after the "Coming soon" fallback.

(function () {
  if (window.__BOT_MGMT_LOADED) return;
  window.__BOT_MGMT_LOADED = true;

  function install() {
    if (typeof NAV_ITEMS === 'undefined' || typeof navigate !== 'function') {
      return setTimeout(install, 80);
    }
    if (!NAV_ITEMS.some(i => i.id === 'bots')) {
      NAV_ITEMS.push({ id: 'bots', icon: '⚙️', label: 'Bot Manage', minRole: 'owner' });
    }
    if (!window.__origNav) {
      window.__origNav = window.navigate;
      window.navigate = function (page) {
        window.__origNav(page);
        if (page === 'bots') {
          var t = document.getElementById('page-title');
          if (t) t.textContent = '⚙️ Bot Manage';
          renderBotsPage();
        }
      };
    }
    // Re-render sidebar if already shown (after login)
    if (typeof renderSidebar === 'function' && typeof admin !== 'undefined' && admin) {
      try { renderSidebar(); } catch (e) {}
    }
  }
  install();

  async function renderBotsPage() {
    const c = document.getElementById('page-content');
    if (!c) return;
    c.innerHTML = `
      <div style="max-width:1100px">
        <div style="background:var(--surface,#1a2030);border:1px solid var(--border,#2a3245);border-radius:10px;padding:20px">
          <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">
            <h2 style="margin:0">🤖 Bot Tokens</h2>
            <button onclick="refreshBots()" style="padding:6px 12px;background:var(--surface-2,#2a3245);color:inherit;border:1px solid var(--border,#3a4255);border-radius:6px;cursor:pointer">🔄 Refresh</button>
          </div>
          <div id="bots-status" style="opacity:0.7;margin-top:8px">⏳ Loading…</div>
          <div style="overflow-x:auto;margin-top:12px">
            <table id="bots-table" style="width:100%;border-collapse:collapse;display:none">
              <thead><tr style="text-align:left;border-bottom:1px solid var(--border,#2a3245)">
                <th style="padding:8px;font-size:12px;opacity:0.7;text-transform:uppercase">Key</th>
                <th style="padding:8px;font-size:12px;opacity:0.7;text-transform:uppercase">Service</th>
                <th style="padding:8px;font-size:12px;opacity:0.7;text-transform:uppercase">Token</th>
                <th style="padding:8px;font-size:12px;opacity:0.7;text-transform:uppercase">Live</th>
                <th style="padding:8px;font-size:12px;opacity:0.7;text-transform:uppercase">Actions</th>
              </tr></thead>
              <tbody></tbody>
            </table>
          </div>
        </div>

        <div style="background:var(--surface,#1a2030);border:1px solid var(--border,#2a3245);border-radius:10px;padding:20px;margin-top:20px">
          <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">
            <h2 style="margin:0">👤 Admin Telegram IDs</h2>
            <button onclick="refreshAdmins()" style="padding:6px 12px;background:var(--surface-2,#2a3245);color:inherit;border:1px solid var(--border,#3a4255);border-radius:6px;cursor:pointer">🔄 Refresh</button>
          </div>
          <div id="admins-status" style="opacity:0.7;margin-top:8px">⏳ Loading…</div>
          <div style="display:flex;gap:10px;margin-top:12px;flex-wrap:wrap">
            <input id="new-admin-id" type="number" placeholder="เพิ่ม Telegram ID (เช่น 8343620146)" style="flex:1;min-width:200px;padding:8px 12px;background:var(--surface-2,#0d1118);color:inherit;border:1px solid var(--border,#2a3245);border-radius:6px">
            <button onclick="addAdmin()" style="padding:8px 16px;background:#4361ee;color:#fff;border:none;border-radius:6px;cursor:pointer;font-weight:600">➕ Add</button>
          </div>
          <ul id="admins-list" style="list-style:none;padding:0;margin:12px 0"></ul>
        </div>
      </div>

      <div id="bot-edit-modal" style="display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.7);z-index:9999;justify-content:center;align-items:center">
        <div style="background:var(--surface,#1a2030);padding:24px;border-radius:10px;max-width:600px;width:90%">
          <h3 style="margin:0 0 8px 0">เปลี่ยน Bot Token: <code id="bot-edit-key" style="background:rgba(0,0,0,0.2);padding:2px 6px;border-radius:4px"></code></h3>
          <p style="opacity:0.7;font-size:14px">หา token ใหม่จาก <a href="https://t.me/BotFather" target="_blank" style="color:#4361ee">@BotFather</a> → /mybots → API Token</p>
          <label style="display:block;margin-top:8px;font-size:14px">New Token:</label>
          <input id="bot-edit-token" type="text" placeholder="paste token เต็มที่นี่" style="width:100%;padding:8px 12px;background:rgba(0,0,0,0.2);color:inherit;border:1px solid var(--border,#2a3245);border-radius:6px;font-family:monospace;margin-top:4px">
          <div id="bot-edit-status" style="min-height:24px;margin-top:8px;font-size:14px"></div>
          <div style="display:flex;gap:10px;justify-content:flex-end;margin-top:12px">
            <button onclick="closeBotEdit()" style="padding:8px 14px;background:var(--surface-2,#2a3245);color:inherit;border:none;border-radius:6px;cursor:pointer">Cancel</button>
            <button id="bot-edit-save" onclick="saveBotToken()" style="padding:8px 14px;background:#4361ee;color:#fff;border:none;border-radius:6px;cursor:pointer;font-weight:600">💾 Save + Restart</button>
          </div>
        </div>
      </div>
    `;
    refreshBots();
    refreshAdmins();
  }

  async function refreshBots() {
    const st = document.getElementById('bots-status');
    const tbl = document.getElementById('bots-table');
    const tb = tbl ? tbl.querySelector('tbody') : null;
    if (!st || !tbl || !tb) return;
    st.textContent = '⏳ Loading bots… (checking getMe with Telegram)';
    tbl.style.display = 'none';
    tb.innerHTML = '';
    try {
      const d = await api('/admin/bots');
      st.textContent = '';
      tbl.style.display = '';
      for (const b of d.bots) {
        const tr = document.createElement('tr');
        tr.style.borderBottom = '1px solid var(--border,#2a3245)';
        const live = b.live || {};
        const liveHtml = live.ok
          ? `<span style="background:rgba(40,180,99,0.2);color:#28b463;padding:3px 8px;border-radius:4px;font-size:12px;font-weight:600">@${esc(live.username)}</span>`
          : (b.has_token
            ? `<span style="background:rgba(231,76,60,0.2);color:#e74c3c;padding:3px 8px;border-radius:4px;font-size:12px">${esc(live.error)}</span>`
            : `<span style="background:rgba(255,170,0,0.2);color:#fa0;padding:3px 8px;border-radius:4px;font-size:12px">no token</span>`);
        tr.innerHTML = `
          <td style="padding:8px;font-family:monospace;font-size:13px">${esc(b.key)}</td>
          <td style="padding:8px;font-family:monospace;font-size:13px">${esc(b.service)}</td>
          <td style="padding:8px;font-family:monospace;font-size:13px;opacity:0.8">${esc(b.token_masked || '(empty)')}</td>
          <td style="padding:8px">${liveHtml}</td>
          <td style="padding:8px">
            <button onclick="testBot('${esc(b.key)}')" style="padding:4px 10px;font-size:12px;background:var(--surface-2,#2a3245);color:inherit;border:1px solid var(--border,#3a4255);border-radius:4px;cursor:pointer">🧪 Test</button>
            <button onclick="openBotEdit('${esc(b.key)}')" style="padding:4px 10px;font-size:12px;background:#4361ee;color:#fff;border:none;border-radius:4px;cursor:pointer;margin-left:4px">✏️ Change</button>
          </td>`;
        tb.appendChild(tr);
      }
    } catch (e) {
      st.innerHTML = `❌ <span style="color:#e74c3c">${esc(e.message)}</span>`;
    }
  }

  async function testBot(key) {
    try {
      const r = await api(`/admin/bots/${key}/test`, { method: 'POST' });
      alert(r.ok ? `✅ ${key}\n@${r.username} (id ${r.id})` : `❌ ${key}\n${r.error}`);
    } catch (e) { alert('Error: ' + e.message); }
  }

  function openBotEdit(key) {
    document.getElementById('bot-edit-key').textContent = key;
    document.getElementById('bot-edit-token').value = '';
    document.getElementById('bot-edit-status').textContent = '';
    document.getElementById('bot-edit-modal').style.display = 'flex';
  }
  function closeBotEdit() {
    document.getElementById('bot-edit-modal').style.display = 'none';
  }

  async function saveBotToken() {
    const key = document.getElementById('bot-edit-key').textContent;
    const t = document.getElementById('bot-edit-token').value.trim();
    const st = document.getElementById('bot-edit-status');
    const btn = document.getElementById('bot-edit-save');
    if (!t || t.length < 20) { st.innerHTML = '<span style="color:#e74c3c">⚠️ Token สั้นเกินไป</span>'; return; }
    if (!/^\d+:[A-Za-z0-9_-]{30,}$/.test(t)) { st.innerHTML = '<span style="color:#e74c3c">⚠️ Format ผิด</span>'; return; }
    btn.disabled = true;
    st.textContent = '⏳ บันทึก + restart container…';
    try {
      const r = await api(`/admin/bots/${key}`, { method: 'PUT', body: JSON.stringify({ token: t }) });
      st.innerHTML = `<span style="color:#28b463">✅ saved + restarted @${esc(r.username)} (${esc(r.service)})</span>`;
      setTimeout(() => { closeBotEdit(); refreshBots(); }, 1500);
    } catch (e) {
      st.innerHTML = `<span style="color:#e74c3c">❌ ${esc(e.message)}</span>`;
    } finally {
      btn.disabled = false;
    }
  }

  async function refreshAdmins() {
    const st = document.getElementById('admins-status');
    const ls = document.getElementById('admins-list');
    if (!st || !ls) return;
    st.textContent = '⏳ Loading…';
    ls.innerHTML = '';
    try {
      const d = await api('/admin/admin-ids');
      st.textContent = `Total: ${d.ids.length} admin IDs`;
      for (const id of d.ids) {
        const li = document.createElement('li');
        li.style.cssText = 'padding:10px;background:rgba(0,0,0,0.2);border-radius:6px;margin-bottom:6px;display:flex;justify-content:space-between;align-items:center';
        li.innerHTML = `<code style="font-family:monospace">${id}</code>
          <button onclick="removeAdmin(${id})" style="padding:4px 10px;font-size:12px;background:#e74c3c;color:#fff;border:none;border-radius:4px;cursor:pointer">🗑️ Remove</button>`;
        ls.appendChild(li);
      }
    } catch (e) {
      st.innerHTML = `❌ <span style="color:#e74c3c">${esc(e.message)}</span>`;
    }
  }

  async function addAdmin() {
    const inp = document.getElementById('new-admin-id');
    const id = parseInt(inp.value, 10);
    if (!id || id <= 0) { alert('กรอก Telegram ID เป็นตัวเลข'); return; }
    if (!confirm(`เพิ่ม admin ID ${id}?\n\nระบบจะ restart bots ที่เกี่ยวข้อง (admin/sales/guardian/content)`)) return;
    try {
      await api('/admin/admin-ids', { method: 'POST', body: JSON.stringify({ telegram_id: id }) });
      inp.value = '';
      await refreshAdmins();
      alert(`✅ Added admin ${id}`);
    } catch (e) { alert('❌ ' + e.message); }
  }

  async function removeAdmin(id) {
    if (!confirm(`ลบ admin ${id}? ระบบจะ restart bots`)) return;
    try {
      await api(`/admin/admin-ids/${id}`, { method: 'DELETE' });
      await refreshAdmins();
      alert(`✅ Removed admin ${id}`);
    } catch (e) { alert('❌ ' + e.message); }
  }

  function esc(s) {
    if (s == null) return '';
    return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c]);
  }

  // expose for inline onclick handlers
  window.refreshBots = refreshBots;
  window.testBot = testBot;
  window.openBotEdit = openBotEdit;
  window.closeBotEdit = closeBotEdit;
  window.saveBotToken = saveBotToken;
  window.refreshAdmins = refreshAdmins;
  window.addAdmin = addAdmin;
  window.removeAdmin = removeAdmin;
})();
                                                                    