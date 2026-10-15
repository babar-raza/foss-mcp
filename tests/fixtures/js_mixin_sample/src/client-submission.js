/**
 * Mixin that adds the submission methods to the client.
 */
export const SubmissionClientMixin = {
  /**
   * Send an email.
   * @param {string} accountId
   * @param {object} email
   */
  async send(accountId, email) {
    return { accountId, email };
  },

  /**
   * Cancel a pending submission.
   * @param {string} accountId
   * @param {string} id
   */
  async cancelSend(accountId, id) {
    return { accountId, id };
  },
};

/**
 * A mixin that nothing applies, so none of its members is published.
 */
export const UnusedMixin = {
  /** Never applied. */
  neverApplied() {
    return 0;
  },
};
