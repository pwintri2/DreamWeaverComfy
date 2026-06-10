const state = {
  view: "comic",
  desire: "",
  currentDream: null,
  status: null,
  isPaused: false,
};

const subscribers = new Set();

export function getState() {
  return structuredClone(state);
}

export function setState(patch) {
  Object.assign(state, patch);
  subscribers.forEach((subscriber) => subscriber(getState()));
}

export function subscribe(subscriber) {
  subscribers.add(subscriber);
  subscriber(getState());
  return () => subscribers.delete(subscriber);
}
