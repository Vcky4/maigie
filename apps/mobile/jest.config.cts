/// <reference types="jest" />
/// <reference types="node" />
module.exports = {
  displayName: 'mobile',
  resolver: require.resolve('./jest.resolver.js'),
  preset: 'jest-expo',
  moduleFileExtensions: ['ts', 'js', 'html', 'tsx', 'jsx'],
  setupFilesAfterEnv: ['<rootDir>/src/test-setup.ts'],
  moduleNameMapper: {
    '\\.svg$': '@nx/expo/plugins/jest/svg-mock',
  },
  transform: {
    '\\.[jt]sx?$': [
      'babel-jest',
      {
        configFile: __dirname + '/.babelrc.js',
      },
    ],
  },
  coverageDirectory: '../../coverage/apps/mobile',
};
