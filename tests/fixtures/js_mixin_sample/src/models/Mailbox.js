/**
 * Represents a named set of Emails.
 *
 * @class Mailbox
 */
export class Mailbox {
  /**
   * Create a mailbox.
   *
   * @param {object} params
   * @param {string} params.name - Human readable name (required).
   * @param {string} [params.id] - Server assigned identifier.
   * @param {string|null} [params.parentId=null] - Identifier of the parent mailbox.
   * @param {number} [params.sortOrder=0] - Ordering hint.
   */
  constructor({ id, name, parentId = null, sortOrder = 0 } = {}) {
    this.id = id;
    this.name = name;
    this.parentId = parentId;
    this.sortOrder = sortOrder;
  }

  /**
   * Describe the mailbox.
   * @param {string} prefix
   */
  describe(prefix) {
    return prefix + this.name;
  }
}
