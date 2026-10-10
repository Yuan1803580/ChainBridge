import { StateStore } from "./state.ts";

const databasePath = process.env.CHAIN_WORKER_DB ?? "./data/chain-worker.db";
const state = new StateStore(databasePath);
const [command = "list", eventId] = process.argv.slice(2);

try {
  if (command === "list") {
    for (const event of state.deadLetters()) {
      console.log(JSON.stringify(event));
    }
  } else if (command === "replay" && eventId) {
    if (!state.replayDeadLetter(eventId)) throw new Error(`Dead-letter event ${eventId} was not found`);
    console.log(`requeued ${eventId}`);
  } else {
    throw new Error("Usage: dead-letter.ts list | replay <eventId>");
  }
} finally {
  state.close();
}
