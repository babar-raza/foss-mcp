/** First mixin of the hub. */
export const AMixin = {
  /** Alpha method. */
  alpha(x) {
    return x;
  },
};

/** Second mixin of the hub. */
export const BMixin = {
  /** Beta method. */
  async beta(y = 1) {
    return y;
  },
};
