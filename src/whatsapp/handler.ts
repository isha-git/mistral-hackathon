import type { IncomingMessage } from "./message.js";

const API_URL = process.env.API_URL ?? "http://api:8000";

export type Reply =
  | { type: "text"; text: string }
  | { type: "audio"; buffer: Buffer; mimetype: string }
  | { type: "image"; buffer: Buffer; mimetype: string; caption?: string }
  | { type: "document"; buffer: Buffer; mimetype: string; filename: string; caption?: string };

type ApiReply = {
  type: "text" | "audio" | "image" | "document";
  text?: string;
  data_base64?: string;
  mimetype?: string;
  caption?: string;
  filename?: string;
};

function buildRequestBody(msg: IncomingMessage): string {
  return JSON.stringify({
    sender: msg.from,
    type: msg.type,
    text: msg.text ?? null,
    urls: msg.urls ?? null,
    media_base64: msg.media ? msg.media.buffer.toString("base64") : null,
    mimetype: msg.media?.mimetype ?? null,
  });
}

function parseApiReply(data: ApiReply): Reply {
  if (data.type === "audio" && data.data_base64) {
    return {
      type: "audio",
      buffer: Buffer.from(data.data_base64, "base64"),
      mimetype: data.mimetype ?? "audio/mp4",
    };
  }

  if (data.type === "image" && data.data_base64) {
    return {
      type: "image",
      buffer: Buffer.from(data.data_base64, "base64"),
      mimetype: data.mimetype ?? "image/jpeg",
      caption: data.caption,
    };
  }

  if (data.type === "document" && data.data_base64) {
    return {
      type: "document",
      buffer: Buffer.from(data.data_base64, "base64"),
      mimetype: data.mimetype ?? "application/octet-stream",
      filename: data.filename ?? "file",
      caption: data.caption,
    };
  }

  return { type: "text", text: data.text ?? "" };
}

export async function handleMessage(msg: IncomingMessage): Promise<Reply> {
  const res = await fetch(`${API_URL}/webhook`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: buildRequestBody(msg),
    signal: AbortSignal.timeout(30_000),
  });

  if (!res.ok) {
    const body = await res.text().catch(() => "");
    console.error(`[handler] API error ${res.status}: ${body}`);
    return { type: "text", text: "Sorry, something went wrong." };
  }

  const data = (await res.json()) as ApiReply;
  return parseApiReply(data);
}
