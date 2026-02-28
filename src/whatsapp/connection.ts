import makeWASocket, {
  useMultiFileAuthState,
  fetchLatestBaileysVersion,
  makeCacheableSignalKeyStore,
  DisconnectReason,
  type WASocket,
} from "@whiskeysockets/baileys";
import qrcode from "qrcode-terminal";
import path from "node:path";
import fs from "node:fs";

// In Docker, WORKDIR=/app and .auth/ is mounted as a volume
const AUTH_DIR = path.join(process.cwd(), ".auth");

// Baileys expects a pino-compatible logger; silence it
const logger = {
  level: "silent",
  child: () => logger,
  trace: () => {},
  debug: () => {},
  info: () => {},
  warn: () => {},
  error: () => {},
  fatal: () => {},
} as unknown as Parameters<typeof makeWASocket>[0] extends { logger?: infer L } ? NonNullable<L> : never;

// Serialize credential saves to prevent corruption
let credsSaveQueue: Promise<void> = Promise.resolve();

function enqueueSaveCreds(saveCreds: () => Promise<void>): void {
  credsSaveQueue = credsSaveQueue
    .then(async () => {
      const credsPath = path.join(AUTH_DIR, "creds.json");
      const backupPath = credsPath + ".bak";
      // Backup before saving
      if (fs.existsSync(credsPath)) {
        fs.copyFileSync(credsPath, backupPath);
      }
      await saveCreds();
    })
    .catch((err) => {
      console.error("[creds] save error:", err);
    });
}

function maybeRestoreCredsFromBackup(): void {
  const credsPath = path.join(AUTH_DIR, "creds.json");
  const backupPath = credsPath + ".bak";

  if (fs.existsSync(credsPath)) {
    try {
      JSON.parse(fs.readFileSync(credsPath, "utf-8"));
      return; // valid, nothing to do
    } catch {
      console.warn("[creds] corrupted, attempting backup restore");
    }
  }

  if (fs.existsSync(backupPath)) {
    try {
      JSON.parse(fs.readFileSync(backupPath, "utf-8"));
      fs.copyFileSync(backupPath, credsPath);
      console.log("[creds] restored from backup");
    } catch {
      console.error("[creds] backup also corrupted");
    }
  }
}

export async function createBotSocket(): Promise<WASocket> {
  fs.mkdirSync(AUTH_DIR, { recursive: true });
  maybeRestoreCredsFromBackup();

  const { version } = await fetchLatestBaileysVersion();
  const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);

  const sock = makeWASocket({
    auth: {
      creds: state.creds,
      keys: makeCacheableSignalKeyStore(state.keys, logger),
    },
    version,
    logger,
    printQRInTerminal: false,
    browser: ["WhatsApp Bridge", "CLI", "1.0.0"],
    syncFullHistory: false,
    markOnlineOnConnect: false,
  });

  // Persist credentials on update
  sock.ev.on("creds.update", () => {
    enqueueSaveCreds(saveCreds);
  });

  // Display QR code in terminal
  sock.ev.on("connection.update", (update) => {
    if (update.qr) {
      console.log("\nScan this QR code with WhatsApp > Linked Devices:\n");
      qrcode.generate(update.qr, { small: true });
    }
  });

  return sock;
}

export function waitForConnection(sock: WASocket): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    const handler = (update: { connection?: string; lastDisconnect?: { error?: Error } }) => {
      if (update.connection === "open") {
        sock.ev.off("connection.update", handler);
        console.log("Connected!");
        resolve();
        return;
      }

      if (update.connection === "close") {
        const err = update.lastDisconnect?.error;
        const statusCode =
          (err as { output?: { statusCode?: number } })?.output?.statusCode ??
          (err as { status?: number })?.status;

        if (statusCode === DisconnectReason.loggedOut) {
          sock.ev.off("connection.update", handler);
          reject(new Error("Logged out. Delete .auth/ and scan QR again."));
          return;
        }

        // Status 515 = restart requested after pairing; other codes = retry
        console.log(`Connection closed (status ${statusCode}), reconnecting...`);
      }
    };

    sock.ev.on("connection.update", handler);
  });
}
