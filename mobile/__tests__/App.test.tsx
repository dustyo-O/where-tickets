/**
 * @format
 */

import React from 'react';
import ReactTestRenderer from 'react-test-renderer';
import App from '../App';

test('renders without crashing and shows the system status label', async () => {
  let tree: ReactTestRenderer.ReactTestRenderer | undefined;

  await ReactTestRenderer.act(() => {
    tree = ReactTestRenderer.create(<App />);
  });

  expect(tree).toBeDefined();
  const serialized = JSON.stringify(tree!.toJSON());
  expect(serialized).toContain('All systems OK');
});
