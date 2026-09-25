/**
 * WhatsApp bridge for the WorkSimplified connectors service.
 *
 * Uses Baileys to run a WhatsApp Web session (pair by QR — no Meta business
 * account, no public webhook). Inbound messages are POSTed to the Python
 * connectors service; outbound replies arrive over a small local HTTP API:
 *
 *   WhatsApp  --messages.upsert-->  bridge  --POST-->  python /channels/whatsapp/inbound
 *   python    --POST /send------->  bridge  --sendMessage-->  WhatsApp
 *
 * Local HTTP API:
 *   GET  /health  { ok, connected, mode, inbound, has_qr }
 *   GET  /qr      { ok, connected, qr }   the pairing code, so the console can show
 *                                         the QR instead of the terminal
 *   POST /send    { to, text }
 *
 * Config (env):
 *   BRIDGE_PORT            port for the local HTTP API            (default 8081)
 *   PY_INBOUND_URL         where to post inbound messages         (default :8765/...)
 *   WHATSAPP_SESSION_DIR   Baileys auth state (treat like a password, chmod 700)
 *   WHATSAPP_MODE          "bot" (dedicated number) | "self-chat"  (default bot)
 *   WHATSAPP_DEBUG         "true" for connection logs             (default false)
 *
 * Run: npm install && node index.js
 */

import http from 'node:http'
import path from 'node:path'
import process from 'node:process'
import * as baileys from '@whiskeysockets/baileys'
import qrcode from 'qrcode-terminal'

const makeWASocket = baileys.default ?? baileys.makeWASocket
const { useMultiFileAuthState, DisconnectReason } = baileys

const PORT = Number(process.env.BRIDGE_PORT || 8081)
const SESSION_DIR = process.env.WHATSAPP_SESSION_DIR ||
  path.resolve(process.cwd(), 'session')
const PY_INBOUND_URL = process.env.PY_INBOUND_URL ||
  `http://127.0.0.1:${process.env.CONNECTORS_PORT || 8765}/channels/whatsapp/inbound`
const MODE = (process.env.WHATSAPP_MODE || 'bot').toLowerCase()
const DEBUG = (process.env.WHATSAPP_DEBUG || 'false').toLowerCase() === 'true'

// Baileys wants a pino-like logger; a tiny stub avoids another dependency.
const silent = {
  level: 'silent',
  trace() {}, debug() {}, info() {}, warn() {}, error() {}, fatal() {},
  child() { return silent },
}

let sock = null
let connected = false
// Baileys hands over a fresh pairing code every ~20s until it is scanned, so the
// console polls this rather than showing a stale QR.
let latestQr = ''

function textOf(message) {
  const m = message?.message
  if (!m) return ''
  return (m.conversation ||
    m.extendedTextMessage?.text ||
    m.imageMessage?.caption ||
    m.videoMessage?.caption ||
    '').trim()
}

async function forwardInbound({ from, text, ts }) {
  try {
    const resp = await fetch(PY_INBOUND_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ from, text, ts }),
    })
    if (!resp.ok) {
      console.error(`bridge: inbound rejected HTTP ${resp.status}`)
    }
  } catch (e) {
    console.error(`bridge: could not reach the python service (${e.message})`)
  }
}

async function startSocket() {
  const { state, saveCreds } = await useMultiFileAuthState(SESSION_DIR)
  sock = makeWASocket({
    auth: state,
    logger: silent,
    printQRInTerminal: false,
    browser: ['WorkSimplified', 'Chrome', '1.0.0'],
  })

  sock.ev.on('creds.update', saveCreds)

  sock.ev.on('connection.update', (update) => {
    const { connection, lastDisconnect, qr } = update
    if (qr) {
      latestQr = qr
      console.log('\nScan this QR in WhatsApp → Settings → Linked Devices → Link a Device:\n')
      console.log('(or open the Connectors tab in the console and scan it there)\n')
      qrcode.generate(qr, { small: true })
    }
    if (connection === 'open') {
      connected = true
      latestQr = ''
      console.log('bridge: connected to WhatsApp')
    }
    if (connection === 'close') {
      connected = false
      const code = lastDisconnect?.error?.output?.statusCode
      const loggedOut = code === DisconnectReason.loggedOut
      console.log(`bridge: connection closed (code ${code}${loggedOut ? ', logged out' : ''})`)
      if (loggedOut) {
        console.log('bridge: session invalidated — delete the session dir and re-pair.')
      } else {
        setTimeout(startSocket, 3000)
      }
    }
    if (DEBUG) console.log('bridge: connection.update', JSON.stringify(update).slice(0, 300))
  })

  sock.ev.on('messages.upsert', async ({ messages, type }) => {
    if (type !== 'notify') return
    for (const msg of messages || []) {
      const jid = msg.key?.remoteJid || ''
      if (!jid || jid.endsWith('@g.us')) continue          // skip groups (demo scope)
      if (msg.key?.fromMe && MODE !== 'self-chat') continue // skip our own sends
      const text = textOf(msg)
      if (!text) continue
      await forwardInbound({ from: jid, text, ts: Number(msg.messageTimestamp || 0) })
    }
  })
}

const server = http.createServer((req, res) => {
  const json = (code, body) => {
    res.writeHead(code, { 'Content-Type': 'application/json' })
    res.end(JSON.stringify(body))
  }

  if (req.method === 'GET' && req.url === '/health') {
    return json(200, { ok: true, connected, mode: MODE, inbound: PY_INBOUND_URL,
                       has_qr: Boolean(latestQr) })
  }

  if (req.method === 'GET' && req.url === '/qr') {
    return json(200, { ok: true, connected, qr: latestQr })
  }

  if (req.method === 'POST' && req.url === '/send') {
    let body = ''
    req.on('data', (c) => { body += c })
    req.on('end', async () => {
      try {
        const { to, text } = JSON.parse(body || '{}')
        if (!to || !text) return json(400, { ok: false, error: 'to and text are required' })
        if (!sock || !connected) return json(503, { ok: false, error: 'not connected' })
        const jid = String(to).includes('@') ? String(to) : `${to}@s.whatsapp.net`
        await sock.sendMessage(jid, { text: String(text) })
        return json(200, { ok: true })
      } catch (e) {
        return json(500, { ok: false, error: String(e).slice(0, 200) })
      }
    })
    return
  }

  return json(404, { ok: false, error: 'no route' })
})

server.listen(PORT, () => {
  console.log(`bridge: listening on :${PORT} (mode=${MODE}, session=${SESSION_DIR})`)
  console.log(`bridge: inbound -> ${PY_INBOUND_URL}`)
})

startSocket().catch((e) => {
  console.error('bridge: failed to start', e)
  process.exit(1)
})
