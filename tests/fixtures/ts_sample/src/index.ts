/** A small interface. */
export interface A {
  x?: number;
  readonly y: string;
  z: boolean;
  m?(a: number): void;
}

/** A short union alias. */
export type T = 'a' | 'b';

/** A union alias whose right-hand side is longer than the limit. */
export type ArrowShapeType =
  | 'arrow'
  | 'rightArrow'
  | 'leftArrow'
  | 'upArrow'
  | 'downArrow'
  | 'leftRightArrow'
  | 'upDownArrow'
  | 'quadArrow'
  | 'leftRightUpArrow'
  | 'bentArrow'
  | 'uTurnArrow'
  | 'leftUpArrow'
  | 'bentUpArrow'
  | 'curvedRightArrow'
  | 'curvedLeftArrow'
  | 'curvedUpArrow'
  | 'curvedDownArrow'
  | 'stripedRightArrow'
  | 'notchedRightArrow'
  | 'pentagon'
  | 'chevron'
  | 'rightArrowCallout'
  | 'leftArrowCallout'
  | 'upArrowCallout'
  | 'downArrowCallout'
  | 'leftRightArrowCallout'
  | 'upDownArrowCallout'
  | 'quadArrowCallout'
  | 'circularArrow';

/** An object-typed alias. */
export type Opts = { depth: number; name?: string };

/** Options for opening a document. */
export interface OpenOptions {
  password?: string;
}

/** A document. */
export class Document {
  /** The version of the library. */
  static readonly VERSION = "1";

  /** The title. */
  title?: string;

  /** Create a document. */
  constructor(createDefault: boolean = true) {}

  /** Open a document from a buffer. */
  static async open(buf: Uint8Array, opts: OpenOptions = {}): Promise<Document> {
    return new Document();
  }

  /** Save the document. */
  save(path?: string, flag = true, ...rest: string[]): void {}

  /** Configure the document. */
  configure({ a, b = 2 }: Opts = { depth: 1 }): void {}

  /** @internal */
  allocObject(size: number): number {
    return size;
  }

  /** Not @internalized prose. */
  markModified(): void {}
}

/** @hidden */
export interface HiddenThing {
  q: number;
}
