import type { WAMessage, WASocket } from "@whiskeysockets/baileys";
import { downloadMedia, type MediaContent } from "./media.js";

export type IncomingMessage = {
  from: string;
  type: "text" | "audio" | "image";
  text?: string;
  urls?: string[];
  media?: MediaContent;
};

const URL_REGEX = /https?:\/\/[^\s]+/gi;

function extractText(msg: WAMessage): string | undefined {
  const m = msg.message;
  if (!m) return undefined;
  return (
    m.conversation ??
    m.extendedTextMessage?.text ??
    m.imageMessage?.caption ??
    m.videoMessage?.caption ??
    undefined
  );
}

function extractUrls(text: string | undefined): string[] | undefined {
  if (!text) return undefined;
  const matches = text.match(URL_REGEX);
  return matches && matches.length > 0 ? matches : undefined;
}

function detectType(msg: WAMessage): "text" | "audio" | "image" {
  const m = msg.message;
  if (!m) return "text";
  if (m.audioMessage) return "audio";
  if (m.imageMessage || m.stickerMessage) return "image";
  return "text";
}

/**
 * Parse a raw Baileys message into a structured IncomingMessage.
 * Downloads media if present.
 */
export async function parseMessage(
  msg: WAMessage,
  sock: WASocket,
): Promise<IncomingMessage | null> {
  const from = msg.key.remoteJid;
  if (!from) return null;

  const type = detectType(msg);
  const text = extractText(msg);
  const urls = extractUrls(text);

  let media: MediaContent | undefined;
  if (type === "audio" || type === "image") {
    const downloaded = await downloadMedia(msg, sock);
    if (downloaded) {
      media = downloaded;
    }
  }

  return { from, type, text: text ?? undefined, urls, media };
}
