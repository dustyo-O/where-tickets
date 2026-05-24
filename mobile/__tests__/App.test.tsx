/**
 * @format
 */

import React from 'react';
import ReactTestRenderer from 'react-test-renderer';
import App from '../App';

let resolveHealth: ((value: { kind: 'ok' }) => void) | undefined;

jest.mock('../src/api/client', () => ({
  __esModule: true,
  BACKEND_URL: 'http://localhost:8000',
  fetchHealth: jest.fn(
    () =>
      new Promise(resolve => {
        resolveHealth = resolve;
      }),
  ),
}));

test('renders without crashing and shows the system status label', async () => {
  let tree: ReactTestRenderer.ReactTestRenderer | undefined;

  await ReactTestRenderer.act(() => {
    tree = ReactTestRenderer.create(<App />);
  });

  expect(tree).toBeDefined();

  // Initially, the screen shows the "Checking…" placeholder while the
  // health probe is still pending.
  const initial = JSON.stringify(tree!.toJSON());
  expect(initial).toContain('Checking');

  // Resolve the deferred health probe and flush microtasks so the screen
  // transitions to OK.
  await ReactTestRenderer.act(async () => {
    resolveHealth?.({ kind: 'ok' });
    await Promise.resolve();
  });

  const serialized = JSON.stringify(tree!.toJSON());
  expect(serialized).toContain('All systems OK');
});
