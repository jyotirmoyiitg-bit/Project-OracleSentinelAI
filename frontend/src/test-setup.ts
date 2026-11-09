import '@testing-library/jest-dom/vitest'

class TestResizeObserver implements ResizeObserver {
  constructor(private readonly callback: ResizeObserverCallback) {}

  observe(target: Element) {
    this.callback([{
      target,
      contentRect: { width: 800, height: 220, x: 0, y: 0, top: 0, right: 800, bottom: 220, left: 0, toJSON: () => ({}) },
    } as ResizeObserverEntry], this)
  }

  unobserve() {}
  disconnect() {}
}

globalThis.ResizeObserver = TestResizeObserver
