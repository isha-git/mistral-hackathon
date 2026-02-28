import { downloadMediaMessage, type WASocket, type WAMessage } from "@whiskeysockets/baileys";

export type MediaContent = {
  buffer: Buffer;
  mimetype: string;
};

/**
 * Download media (voice note, image, video, document) from an incoming message.
 * Returns null if the message has no downloadable media.
 */
export async function downloadMedia(
  msg: WAMessage,
  sock: WASocket,
): Promise<MediaContent | null> {
  const message = msg.message;
  if (!message) return null;

  const mimetype =
    message.imageMessage?.mimetype ??
    message.videoMessage?.mimetype ??
    message.audioMessage?.mimetype ??
    message.documentMessage?.mimetype ??
    message.stickerMessage?.mimetype;

  if (!mimetype) return null;

  try {
    const buffer = (await downloadMediaMessage(
      msg,
      "buffer",
      {},
      {
        reuploadRequest: sock.updateMediaMessage,
        logger: sock.logger,
      },
    )) as Buffer;

    return { buffer, mimetype };
  } catch (err) {
    console.error("[media] download failed:", err);
    return null;
  }
}

/** Send a text message. */
export async function sendText(sock: WASocket, jid: string, text: string): Promise<void> {
  await sock.sendMessage(jid, { text });
}

/** Send audio as a voice note (push-to-talk). */
export async function sendAudio(sock: WASocket, jid: string, buffer: Buffer): Promise<void> {
  await sock.sendMessage(jid, {
    audio: buffer,
    mimetype: "audio/mp4",
    ptt: true,
  });
}

/** Send an image with an optional caption. */
export async function sendImage(
  sock: WASocket,
  jid: string,
  buffer: Buffer,
  caption?: string,
): Promise<void> {
  await sock.sendMessage(jid, {
    image: buffer,
    mimetype: "image/jpeg",
    caption,
  });
}
