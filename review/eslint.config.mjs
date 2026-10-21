export default [{
  files: ['**/*.js', '**/*.mjs'],
  languageOptions: {
    ecmaVersion: 'latest',
    sourceType: 'module',
    globals: Object.fromEntries(['AbortController', 'Image', 'Option', 'URL', 'crypto', 'document', 'fetch', 'requestAnimationFrame', 'window'].map((name) => [name, 'readonly'])),
  },
  rules: {
    'no-undef': 'error',
    'no-unused-vars': 'error',
    'no-unreachable': 'error',
    'no-constant-condition': 'error',
    'no-dupe-args': 'error',
    'no-dupe-keys': 'error',
    'valid-typeof': 'error',
    'eqeqeq': 'error',
  },
}]
