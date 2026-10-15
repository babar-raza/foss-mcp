// Composition file: the wrapper client gets its methods from two mixins.

import { JmapClient as CoreClient } from "./client-core.js";
import { MailClientMixin } from "./client-mail.js";
import { SubmissionClientMixin } from "./client-submission.js";

class JmapClient extends CoreClient {}

Object.assign(JmapClient.prototype, MailClientMixin);
Object.assign(JmapClient.prototype, SubmissionClientMixin);

export { JmapClient };
export { Mailbox } from "./models/Mailbox.js";
export { Hub } from "./multi.js";
