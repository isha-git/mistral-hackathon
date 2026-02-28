import { createBotSocket, waitForConnection } from "./connection.js";
import { parseMessage } from "./message.js";
import { handleMessage, type Reply } from "./handler.js";
import { sendText, sendAudio, sendImage } from "./media.js";
import type { WASocket } from "@whiskeysockets/baileys";

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

async function main(): Promise<void> {
  console.log("Starting WhatsApp bridge...");

  const sock = await createBotSocket();
  await waitForConnection(sock);

  sock.ev.on("messages.upsert", async ({ messages, type }) => {
    if (type !== "notify") return;

    for (const msg of messages) {
      // Skip own messages and status broadcasts
      if (msg.key.fromMe) continue;
      if (msg.key.remoteJid === "status@broadcast") continue;

      const parsed = await parseMessage(msg, sock);
      if (!parsed) continue;

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
