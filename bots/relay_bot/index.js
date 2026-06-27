'use strict';
/**
 * Relay Bot v2 — Production-ready
 *
 * Improvements over v1:
 *  - Persistent state (state.json on volume)
 *  - Duplicate detection (skip identical message in 1h)
 *  - Auto-disable bad dest after 5 consecutive failures
 *  - /stats command (admin only)
 *  - /pause /resume (admin only)
 *  - Admin notifications on critical errors (via admin chat)
 *  - Stats counter (forwarded total, failures, last hour)
 *  - Better error categorization
 */

const TelegramBot = require('node-telegram-bot-api');
const express = require('express');
const fs = require('fs');
const path = require('path');
const { Client } = require('pg');  // B.2 (2026-06-27): DB-driven DEST list

// ─── ENV configuration ──────────────────────────
const TOKEN = process.env.BOT_TOKEN;
const SOURCE_CHAT_ID = process.env.SOURCE_CHAT_ID || '-1003625687303';
// B.2 (2026-06-27): DEST list is now refreshed from bot_group_targets DB every 60s.
// Env DEST_CHAT_IDS retained as cold-start fallback only.
const FALLBACK_DEST_CHAT_IDS = (process.env.DEST_CHAT_IDS || '').split(',').map(s => s.trim()).filter(Boolean);
let DEST_CHAT_IDS = [...FALLBACK_DEST_CHAT_IDS];
const DATABASE_URL = process.env.DATABASE_URL || '';
const DEST_REFRESH_MS = 60 * 1000;
const SALES_BOT_URL = process.env.SALES_BOT_URL || 'https://t.me/NamwarnJarern_bot';
const WELCOME_PHOTO = process.env.WELCOME_PHOTO || 'https://img5.pic.in.th/file/secure-sv1/Gemini_Generated_Image_o52ch3o52ch3o52c-copy.jpg';
const CLOSING_COOLDOWN_MS = parseInt(process.env.CLOSING_COOLDOWN_MS || '300000'); // 5 min
const ADMIN_IDS = (process.env.ADMIN_IDS || '8502597269').split(',').map(s => parseInt(s.trim())).filter(Boolean);
const PORT = process.env.PORT || 3010;
const DATA_DIR = process.env.DATA_DIR || '/data';
const DEDUPE_TTL_MS = 60 * 60 * 1000; // 1 hour
const MAX_DEST_FAILURES = 5; // disable after 5 consecutive failures


// ─── B.2: DB-driven DEST refresh ─────────────
async function refreshDestsFromDb() {
  if (!DATABASE_URL) return;
  const client = new Client({ connectionString: DATABASE_URL.replace('postgresql+asyncpg://', 'postgresql://') });
  try {
    await client.connect();
    const r = await client.query(
      "SELECT chat_id::text AS chat_id FROM bot_group_targets " +
      "WHERE bot_key = $1 AND target_role = $2 AND is_active = TRUE",
      ['relay_bot', 'distribution']
    );
    const fresh = r.rows.map(row => row.chat_id);
    if (fresh.length > 0) {
      const prev = DEST_CHAT_IDS.join(',');
      const next = fresh.join(',');
      if (prev !== next) {
        log(`\u{1F4CB} DEST list refreshed from DB: ${fresh.length} groups (was ${DEST_CHAT_IDS.length})`);
        DEST_CHAT_IDS = fresh;
      }
    } else {
      log(`\u26A0\uFE0F DB returned 0 dest groups — keeping current list (${DEST_CHAT_IDS.length})`);
    }
  } catch (e) {
    log(`\u26A0\uFE0F DB dest refresh failed: ${e.message} — keeping current list (${DEST_CHAT_IDS.length})`);
  } finally {
    try { await client.end(); } catch (_) {}
  }
}

if (!TOKEN) {
  console.error('FATAL: BOT_TOKEN env var not set');
  process.exit(1);
}

// ─── Files ──────────────────────────────────────
const LOG_FILE = path.join(DATA_DIR, 'relay.log');
const STATE_FILE = path.join(DATA_DIR, 'state.json');

try { fs.mkdirSync(DATA_DIR, { recursive: true }); } catch (_) {}

function log(msg) {
  const stamp = new Date().toISOString();
  const line = `[${stamp}] ${msg}`;
  console.log(line);
  try { fs.appendFileSync(LOG_FILE, line + '\n'); } catch (_) {}
}

// ─── Persistent state ───────────────────────────
let state = {
  lastClosingAt: 0,
  destFailures: {}, // destId -> count
  destDisabled: {}, // destId -> bool
  paused: false,
  stats: {
    forwarded: 0,
    failed: 0,
    closingsSent: 0,
    startedAt: Date.now(),
    lastForwardAt: 0,
  },
  // recent message dedupe — key = hash(file_id + caption)
  recentHashes: {}, // hash -> timestamp
};

function loadState() {
  try {
    if (fs.existsSync(STATE_FILE)) {
      const data = JSON.parse(fs.readFileSync(STATE_FILE, 'utf-8'));
      state = { ...state, ...data, stats: { ...state.stats, ...(data.stats || {}) } };
      log(`📦 State loaded: forwarded=${state.stats.forwarded}, failed=${state.stats.failed}`);
    }
  } catch (e) { log(`⚠️ State load failed: ${e.message}`); }
}

function saveState() {
  try {
    fs.writeFileSync(STATE_FILE, JSON.stringify(state, null, 2));
  } catch (e) { log(`⚠️ State save failed: ${e.message}`); }
}
loadState();
setInterval(saveState, 30000); // auto-save every 30s

// ─── Dedupe ─────────────────────────────────────
function makeHash(items) {
  // Combine all file_ids + caption for dedupe key
  const key = items.map(x => `${x.type || 'msg'}:${x.media || x.file_id || ''}:${x.caption || ''}`).join('|');
  return require('crypto').createHash('md5').update(key).digest('hex');
}

function isDuplicate(hash) {
  const now = Date.now();
  // Clean old
  for (const k of Object.keys(state.recentHashes)) {
    if (now - state.recentHashes[k] > DEDUPE_TTL_MS) delete state.recentHashes[k];
  }
  if (state.recentHashes[hash] && now - state.recentHashes[hash] < DEDUPE_TTL_MS) return true;
  state.recentHashes[hash] = now;
  return false;
}

// ─── Bot + Server ───────────────────────────────
const bot = new TelegramBot(TOKEN, { polling: true });
const app = express();
const messageQueue = [];
let isProcessingQueue = false;
const mediaGroups = {};

app.get('/', (req, res) => res.send('Relay Bot v2 is alive!'));
app.get('/status', (req, res) => res.json({
  source: SOURCE_CHAT_ID,
  dests: DEST_CHAT_IDS,
  destsActive: DEST_CHAT_IDS.filter(d => !state.destDisabled[d]),
  destsDisabled: Object.keys(state.destDisabled),
  queue: messageQueue.length,
  processing: isProcessingQueue,
  mediaGroups: Object.keys(mediaGroups).length,
  paused: state.paused,
  stats: state.stats,
  uptimeSec: Math.floor((Date.now() - state.stats.startedAt) / 1000),
}));
app.listen(PORT, () => log(`HTTP server on :${PORT}`));

// B.2: Initial DB load + 60s refresh
refreshDestsFromDb().catch(() => {});
setInterval(() => refreshDestsFromDb().catch(() => {}), DEST_REFRESH_MS);

log(`Relay Bot v2 starting — source=${SOURCE_CHAT_ID}, dests=${DEST_CHAT_IDS.length}, admins=${ADMIN_IDS.length}`);

// ─── Polling auto-restart ───────────────────────
bot.on('polling_error', (err) => {
  log(`⚠️ Polling error: ${err.code || err.message}`);
  if (err.code === 'EFATAL' || (err.message || '').includes('terminated') || err.code === 'ETELEGRAM') {
    log('🔄 Polling crashed — restart in 5s');
    bot.stopPolling()
      .then(() => setTimeout(() => bot.startPolling(), 5000))
      .catch(() => setTimeout(() => bot.startPolling(), 5000));
  }
});

// ─── Helpers ────────────────────────────────────
const isAdmin = (userId) => ADMIN_IDS.includes(parseInt(userId));
const fmtDur = (ms) => {
  const s = Math.floor(ms / 1000);
  const h = Math.floor(s / 3600); const m = Math.floor((s % 3600) / 60); const sec = s % 60;
  return h ? `${h}h ${m}m` : (m ? `${m}m ${sec}s` : `${sec}s`);
};

// ─── /id command — anyone can use ──────────────
bot.onText(/^\/id(@\w+)?$/, (msg) => {
  const chatId = msg.chat.id;
  const title = msg.chat.title || msg.chat.first_name || 'this chat';
  bot.sendMessage(chatId,
    `<b>🆔 Chat Info</b>\n` +
    `Type: <code>${msg.chat.type}</code>\n` +
    `Title: <code>${title}</code>\n` +
    `ID: <code>${chatId}</code>`,
    { parse_mode: 'HTML' }
  ).catch(() => {});
});

// ─── /start (private only) ──────────────────────
bot.onText(/^\/start/, (msg) => {
  if (msg.chat.type !== 'private') return;
  const captionText =
    `<b>🌟 ขุมทรัพย์แห่งความบันเทิง! 🌟</b>\n\n` +
    `หาคลิปที่บอทตัวนี้โพสลงกลุ่มใช่หรือไม่\n` +
    `ถ้าท่านอยากโหลดคลิปที่บอทตัวนี้โพส\nสามารถโหลดได้ที่ \nกลุ่ม V-God ใน VIP เจริญพร\n\n` +
    `<i>อย่าปล่อยให้ความสุขหลุดลอยไป...</i>\n` +
    `<i>กดปุ่มด้านล่างเพื่อเข้าสู่ดินแดนศักดิ์สิทธิ์</i> 👇`;
  bot.sendPhoto(msg.chat.id, WELCOME_PHOTO, {
    caption: captionText, parse_mode: 'HTML',
    reply_markup: { inline_keyboard: [[{ text: '👑 สมัครเข้ากลุ่ม VIP เจริญพร 👑', url: SALES_BOT_URL }]] },
  }).catch(err => log(`❌ /start send failed: ${err.message}`));
});

// ─── Admin commands ─────────────────────────────
bot.onText(/^\/stats(@\w+)?$/, (msg) => {
  if (!isAdmin(msg.from.id)) return;
  const s = state.stats;
  const uptime = fmtDur(Date.now() - s.startedAt);
  const lastFwd = s.lastForwardAt ? fmtDur(Date.now() - s.lastForwardAt) + ' ago' : 'never';
  const disabled = Object.keys(state.destDisabled);
  const text =
    `📊 <b>Relay Bot Stats</b>\n\n` +
    `Uptime: <code>${uptime}</code>\n` +
    `Forwarded: <b>${s.forwarded}</b>\n` +
    `Failed: <b>${s.failed}</b>\n` +
    `Closings sent: <b>${s.closingsSent}</b>\n` +
    `Last forward: ${lastFwd}\n\n` +
    `Queue: ${messageQueue.length}\n` +
    `Processing: ${isProcessingQueue ? '🟢' : '🔘'}\n` +
    `Paused: ${state.paused ? '⏸️ YES' : '▶️ NO'}\n\n` +
    `Active dests: <b>${DEST_CHAT_IDS.length - disabled.length}/${DEST_CHAT_IDS.length}</b>\n` +
    (disabled.length ? `Disabled: <code>${disabled.join(', ')}</code>\n` : '');
  bot.sendMessage(msg.chat.id, text, { parse_mode: 'HTML' }).catch(() => {});
});

bot.onText(/^\/pause(@\w+)?$/, (msg) => {
  if (!isAdmin(msg.from.id)) return;
  state.paused = true; saveState();
  bot.sendMessage(msg.chat.id, '⏸️ <b>Bot paused</b> — incoming will be ignored until /resume',
    { parse_mode: 'HTML' }).catch(() => {});
  log(`⏸️ Paused by admin ${msg.from.id}`);
});

bot.onText(/^\/resume(@\w+)?$/, (msg) => {
  if (!isAdmin(msg.from.id)) return;
  state.paused = false; saveState();
  bot.sendMessage(msg.chat.id, '▶️ <b>Bot resumed</b>', { parse_mode: 'HTML' }).catch(() => {});
  log(`▶️ Resumed by admin ${msg.from.id}`);
});

bot.onText(/^\/enable_dest (-?\d+)$/, (msg, match) => {
  if (!isAdmin(msg.from.id)) return;
  const destId = match[1];
  delete state.destFailures[destId];
  delete state.destDisabled[destId];
  saveState();
  bot.sendMessage(msg.chat.id, `✅ Dest ${destId} re-enabled`).catch(() => {});
});

// ─── Source content handler ─────────────────────
const handleMessage = (msg) => {
  if (state.paused) return;
  if (String(msg.chat.id) !== String(SOURCE_CHAT_ID)) return;

  if (msg.media_group_id) {
    const groupId = msg.media_group_id;
    if (!mediaGroups[groupId]) mediaGroups[groupId] = { media: [], timer: null };
    const fileId = getFileId(msg);
    const type = getFileType(msg);
    if (fileId) {
      mediaGroups[groupId].media.push({
        type, media: fileId, caption: msg.caption || '',
        parse_mode: 'HTML', originalMsg: msg,
      });
    }
    if (mediaGroups[groupId].timer) clearTimeout(mediaGroups[groupId].timer);
    mediaGroups[groupId].timer = setTimeout(() => pushAlbumToQueue(groupId), 5000);
  } else {
    if (msg.video || msg.photo || msg.document || msg.animation) {
      const hash = makeHash([{ type: 'single', media: getFileId(msg), caption: msg.caption || '' }]);
      if (isDuplicate(hash)) {
        log(`🔁 Duplicate single skipped`);
        return;
      }
      messageQueue.push({ type: 'single', msg, hash });
      processQueue();
    }
  }
};
bot.on('channel_post', handleMessage);
bot.on('message', handleMessage);

function pushAlbumToQueue(groupId) {
  const group = mediaGroups[groupId];
  if (!group || group.media.length === 0) return;
  const items = group.media;

  // Dedupe whole album
  const albumHash = makeHash(items);
  if (isDuplicate(albumHash)) {
    log(`🔁 Duplicate album skipped (${items.length} items)`);
    delete mediaGroups[groupId];
    return;
  }

  const albumCaption = items.find(m => m.caption && m.caption.trim() !== '')?.caption || '';

  for (let i = 0; i < items.length; i += 10) {
    const chunk = items.slice(i, i + 10);
    if (chunk.length === 1) {
      messageQueue.push({ type: 'single', msg: chunk[0].originalMsg, hash: albumHash });
    } else {
      const mediaToSend = chunk.map((item, idx) => {
        const obj = { type: item.type, media: item.media, parse_mode: 'HTML' };
        if (idx === 0 && albumCaption) obj.caption = albumCaption;
        return obj;
      });
      messageQueue.push({ type: 'album', media: mediaToSend, hash: albumHash });
    }
  }
  delete mediaGroups[groupId];
  processQueue();
}

// ─── Queue processor ────────────────────────────
async function processQueue() {
  if (isProcessingQueue || messageQueue.length === 0) return;
  isProcessingQueue = true;

  while (messageQueue.length > 0) {
    const task = messageQueue.shift();
    let taskHasAnySuccess = false;

    for (const destId of DEST_CHAT_IDS) {
      // Skip disabled dests
      if (state.destDisabled[destId]) continue;

      let success = false;
      let attempt = 0;
      while (!success) {
        attempt++;
        try {
          if (task.type === 'album') {
            await bot.sendMediaGroup(destId, task.media.map(m => ({ ...m })));
          } else {
            await bot.copyMessage(destId, task.msg.chat.id, task.msg.message_id);
          }
          success = true;
          taskHasAnySuccess = true;
          state.stats.forwarded++;
          state.stats.lastForwardAt = Date.now();
          // Reset failure count on success
          if (state.destFailures[destId]) delete state.destFailures[destId];
          log(`✅ ${task.type} → ${destId}`);
          await sleep(3000);
        } catch (err) {
          log(`⚠️ Attempt ${attempt} → ${destId}: ${err.message}`);
          if (err.response && (err.response.statusCode === 400 || err.response.statusCode === 403)) {
            log(`❌ Fatal (${err.response.statusCode}) → skip + tally`);
            success = true; // skip task on this dest
            state.stats.failed++;
            state.destFailures[destId] = (state.destFailures[destId] || 0) + 1;
            if (state.destFailures[destId] >= MAX_DEST_FAILURES) {
              state.destDisabled[destId] = true;
              log(`🚫 AUTO-DISABLED dest ${destId} after ${MAX_DEST_FAILURES} consecutive fails`);
              notifyAdmins(`🚫 Auto-disabled dest <code>${destId}</code> after ${MAX_DEST_FAILURES} failures.\nUse /enable_dest ${destId} to re-enable.`);
            }
          } else if (attempt >= 20) {
            log(`❌ Too many attempts → skip`);
            success = true; state.stats.failed++;
          } else {
            let waitMs = 10000;
            if (err.response && err.response.statusCode === 429) {
              const retryAfter = err.response.body?.parameters?.retry_after || 60;
              waitMs = retryAfter * 1000;
              log(`⏳ Rate limited — wait ${retryAfter}s`);
            }
            await sleep(waitMs);
          }
        }
      }
    }
    await sleep(4000);
  }
  isProcessingQueue = false;
  log('🎉 Queue done');
  saveState();

  // Closing message (debounced 5 min)
  const now = Date.now();
  if (now - state.lastClosingAt < CLOSING_COOLDOWN_MS) {
    log(`⏸️ Closing msg skipped — cooldown (${Math.round((CLOSING_COOLDOWN_MS - (now - state.lastClosingAt))/1000)}s left)`);
    return;
  }
  state.lastClosingAt = now;
  saveState();

  const closingText =
    `<b>🎬 อัปเดตคลิปใหม่</b>\n\n` +
    `🔥 <i>ถ้าท่านอยากโหลดคลิปเหล่านี้ หรือดูแบบเต็มๆ จุใจ...</i>\n` +
    `👇 <b>สามารถโหลดและติดตามได้ที่กลุ่ม V-God VIP เจริญพร</b> 👇`;
  const closingOpts = {
    parse_mode: 'HTML',
    reply_markup: { inline_keyboard: [[{ text: '👑 สมัครเข้ากลุ่ม VIP เจริญพร 👑', url: SALES_BOT_URL }]] },
  };
  for (const destId of DEST_CHAT_IDS) {
    if (state.destDisabled[destId]) continue;
    try {
      await bot.sendMessage(destId, closingText, closingOpts);
      log(`✅ Closing → ${destId}`);
      state.stats.closingsSent++;
      await sleep(1000);
    } catch (err) {
      log(`❌ Closing → ${destId}: ${err.message}`);
    }
  }
  saveState();
}

// ─── Admin notify ───────────────────────────────
async function notifyAdmins(text) {
  for (const adminId of ADMIN_IDS) {
    try { await bot.sendMessage(adminId, text, { parse_mode: 'HTML' }); } catch (_) {}
  }
}

// ─── Helpers ────────────────────────────────────
function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
function getFileId(msg) {
  if (msg.video) return msg.video.file_id;
  if (msg.photo) return msg.photo[msg.photo.length - 1].file_id;
  if (msg.document) return msg.document.file_id;
  if (msg.animation) return msg.animation.file_id;
  return null;
}
function getFileType(msg) {
  if (msg.video) return 'video';
  if (msg.photo) return 'photo';
  if (msg.document) return 'document';
  if (msg.animation) return 'video';
  return 'photo';
}

// ─── Anti-death ─────────────────────────────────
process.on('uncaughtException', err => {
  log(`🔥 uncaughtException: ${err.stack || err}`);
  notifyAdmins(`🚨 <b>Relay Bot crashed</b>\n<code>${(err.message || err).toString().slice(0,200)}</code>`);
});
process.on('unhandledRejection', (reason) => {
  log(`🔥 unhandledRejection: ${reason}`);
});
process.on('SIGTERM', () => { log('SIGTERM — saving state'); saveState(); process.exit(0); });
process.on('SIGINT', () => { log('SIGINT — saving state'); saveState(); process.exit(0); });
