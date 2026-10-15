import { AMixin, BMixin } from "./hub-mixins.js";

/**
 * A hub composed from several mixins.
 */
export class Hub {
  /** Start the hub. */
  start() {
    return true;
  }
}

Object.assign(Hub.prototype, AMixin, BMixin);
Object.assign(Hub.prototype, {
  /** Ping the hub. */
  ping() {
    return "pong";
  },
});
