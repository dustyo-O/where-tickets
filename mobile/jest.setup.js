/**
 * Jest setup — mocks for native modules that don't render in the
 * react-test-renderer environment without layout.
 *
 * @format
 */
/* eslint-env jest */

jest.mock('react-native-safe-area-context', () => {
  const mock = require('react-native-safe-area-context/jest/mock');
  return mock.default ?? mock;
});

jest.mock('react-native-config', () => ({
  __esModule: true,
  default: {},
}));
