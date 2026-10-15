/**
 * Core client that opens a session and sends raw requests.
 */
export class JmapClient {
  /**
   * Create a client.
   * @param {object} options
   */
  constructor(options) {
    this.options = options;
  }

  /**
   * Open the session.
   */
  async connect() {
    return true;
  }

  /**
   * Close the session.
   */
  close() {
    return undefined;
  }

  /**
   * Send one raw request.
   * @param {object} request
   */
  async sendRequest(request) {
    return request;
  }

  _internal() {
    return this.options;
  }
}
