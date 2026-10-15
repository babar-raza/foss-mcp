/**
 * Mixin that adds the mailbox methods to the client.
 */
export const MailClientMixin = {
  /**
   * List the mailboxes of an account.
   * @param {string} accountId
   * @param {string[]|null} [ids=null]
   * @param {string[]|null} [properties=null]
   * @returns {Promise<object[]>}
   */
  async listMailboxes(accountId, ids = null, properties = null) {
    return [accountId, ids, properties];
  },

  /**
   * Get one mailbox.
   * @param {string} accountId
   * @param {string} id
   */
  async getMailbox(accountId, id) {
    return { accountId, id };
  },

  /**
   * Create a mailbox.
   * @param {string} accountId
   * @param {object} mailbox
   */
  async createMailbox(accountId, mailbox) {
    return { accountId, mailbox };
  },

  /**
   * Delete a mailbox.
   * @param {string} accountId
   * @param {string} id
   * @param {...string} rest
   */
  async deleteMailbox(accountId, id, ...rest) {
    return { accountId, id, rest };
  },

  /**
   * Internal helper that must not be published.
   */
  _private() {
    return 1;
  },

  /**
   * Count the characters of an account id with an arrow function.
   * @param {string} accountId
   */
  countMailboxes: (accountId) => accountId.length,
};
