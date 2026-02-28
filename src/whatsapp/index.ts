import http from "node:http";
import { createBotSocket, waitForConnection } from "./connection.js";
import { parseMessage } from "./message.js";
import { handleMessage, type Reply } from "./handler.js";
import { sendText, sendAudio, sendImage } from "./media.js";
import type { WASocket } from "@whiskeysockets/baileys";

const SEND_PORT = parseInt(process.env.SEND_PORT ?? "3001");

async function sendReply(sock: WASocket, jid: string, reply: Reply): Promise<void> {
  switch (reply.type) {
    case "text":
      await sendText(sock, jid, reply.text);
      break;
    case "audio":
      await sendAudio(sock, jid, reply.buffer);
      break;
    case "image":
      await sendImage(sock, jid, reply.buffer, reply.caption);
      break;
  }
}

/**
 * HTTP server that allows our Celery workers to push messages back
 * to WhatsApp users.
 *
 * POST /send
 * {
 *   "to": "+1234567890",
 *   "message": "Agent question or result text"
 * }
 */
function startSendServer(sock: WASocket): void {
  const server = http.createServer(async (req, res) => {
    if (req.method !== "POST" || req.url !== "/send") {
      res.writeHead(404);
      res.end();
      return;
    }

    let body = "";
    req.on("data", (chunk) => (body += chunk));
    req.on("end", async () => {
      try {
        const { to, message } = JSON.parse(body) as { to: string; message: string };

        if (!to || !message) {
          res.writeHead(400);
          res.end(JSON.stringify({ error: "Missing 'to' or 'message'" }));
          return;
        }

        await sendText(sock, to, message);
        console.log(`[send-server] sent message to ${to}`);

        res.writeHead(200);
        res.end(JSON.stringify({ ok: true }));
      } catch (err) {
        console.error("[send-server] error:", err);
        res.writeHead(500);
        res.end(JSON.stringify({ error: "Internal error" }));
      }
    });
  });

  server.listen(SEND_PORT, () => {
    console.log(`[send-server] listening on port ${SEND_PORT}`);
  });
}

async function main(): Promise<void> {
  console.log("Starting WhatsApp bridge...");

  const sock = await createBotSocket();
  await waitForConnection(sock);

  // Start HTTP server so Celery can push messages back to users
  startSendServer(sock);

  sock.ev.on("messages.upsert", async ({ messages, type }) => {
    if (type !== "notify") return;

    for (const msg of messages) {
      // Skip own messages and status broadcasts
      if (msg.key.fromMe) continue;
      if (msg.key.remoteJid === "status@broadcast") continue;

      const parsed = await parseMessage(msg, sock);
      if (!parsed) continue;

      // Skip our own responses (echoed messages)
      if (parsed.text && (
        parsed.text.includes("Got it! Working on:") ||
        parsed.text.includes("Job ID:") ||
        parsed.text.includes("I'll message you when done")
      )) {
        console.log(`[skip] Ignoring echoed bot message from ${parsed.from}`);
        continue;
      }

      console.log(`[in] ${parsed.type} from ${parsed.from}: ${parsed.text ?? "(media)"}`);

      try {
        const reply = await handleMessage(parsed);
        await sendReply(sock, parsed.from, reply);
        console.log(`[out] ${reply.type} to ${parsed.from}`);
      } catch (err) {
        console.error(`[error] handling message from ${parsed.from}:`, err);
        await sendText(sock, parsed.from, "Sorry, something went wrong.").catch(() => {});
      }
    }
  });

  console.log("Bridge is running. Press Ctrl+C to stop.");
}

main().catch((err) => {
  console.error("Fatal error:", err);
  process.exit(1);
});
