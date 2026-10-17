// Composition file: the wrapper client gets its methods from two mixins.

import { SampleClient as CoreClient } from "./client-core.js";
import { MailClientMixin } from "./client-mail.js";
import { SubmissionClientMixin } from "./client-submission.js";

class SampleClient extends CoreClient {}

Object.assign(SampleClient.prototype, MailClientMixin);
Object.assign(SampleClient.prototype, SubmissionClientMixin);

export { SampleClient };
export { Mailbox } from "./models/Mailbox.js";
export { Hub } from "./multi.js";
