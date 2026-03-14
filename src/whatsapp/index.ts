import http from "node:http";
import { createBotSocket, waitForConnection } from "./connection.js";
import { parseMessage } from "./message.js";
import { handleMessage, type Reply } from "./handler.js";
import { sendText, sendAudio, sendImage, sendDocument } from "./media.js";
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
    case "document":
      await sendDocument(sock, jid, reply.buffer, reply.filename, reply.mimetype, reply.caption);
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
function startSendServer(sock: WASocket): http.Server {
  const server = http.createServer(async (req, res) => {
    if (req.method !== "POST" || (req.url !== "/send" && req.url !== "/send-document")) {
      res.writeHead(404);
      res.end();
      return;
    }

    const route = req.url;
    const MAX_BODY_SIZE = 10 * 1024 * 1024; // 10MB
    let body = "";
    let bodySize = 0;
    req.on("data", (chunk: Buffer | string) => {
      bodySize += typeof chunk === "string" ? Buffer.byteLength(chunk) : chunk.length;
      if (bodySize > MAX_BODY_SIZE) {
        res.writeHead(413);
        res.end(JSON.stringify({ error: "Request body too large" }));
        req.destroy();
        return;
      }
      body += chunk;
    });
    req.on("end", async () => {
      if (bodySize > MAX_BODY_SIZE) return;
      try {
        const parsed = JSON.parse(body);

        if (route === "/send-document") {
          const { to, data_base64, filename, mimetype, caption } = parsed as {
            to: string; data_base64: string; filename: string; mimetype?: string; caption?: string;
          };
          if (!to || !data_base64 || !filename) {
            res.writeHead(400);
            res.end(JSON.stringify({ error: "Missing 'to', 'data_base64', or 'filename'" }));
            return;
          }
          const buffer = Buffer.from(data_base64, "base64");
          await sendDocument(sock, to, buffer, filename, mimetype ?? "application/octet-stream", caption);
          console.log(`[send-server] sent document '${filename}' to ${to}`);
        } else {
          const { to, message } = parsed as { to: string; message: string };
          if (!to || !message) {
            res.writeHead(400);
            res.end(JSON.stringify({ error: "Missing 'to' or 'message'" }));
            return;
          }
          await sendText(sock, to, message);
          console.log(`[send-server] sent message to ${to}`);
        }

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

  return server;
}

async function main(): Promise<void> {
  console.log("Starting WhatsApp bridge...");

  const sock = await createBotSocket();
  await waitForConnection(sock);

  // Start HTTP server so Celery can push messages back to users
  const server = startSendServer(sock);

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

  // Graceful shutdown — close HTTP server and WhatsApp socket cleanly
  // so Docker stop / compose down doesn't cause "logged out" errors on reconnect
  function shutdown(signal: string): void {
    console.log(`\n${signal} received, shutting down...`);
    server.close(() => {
      console.log("[send-server] closed");
      sock.end(undefined);
      console.log("[whatsapp] socket closed");
      process.exit(0);
    });
    // Force exit if graceful close takes too long
    setTimeout(() => {
      console.error("Shutdown timed out, forcing exit");
      process.exit(1);
    }, 10_000);
  }

  process.on("SIGTERM", () => shutdown("SIGTERM"));
  process.on("SIGINT", () => shutdown("SIGINT"));

  console.log("Bridge is running. Press Ctrl+C to stop.");
}

main().catch((err) => {
  console.error("Fatal error:", err);
  process.exit(1);
});
