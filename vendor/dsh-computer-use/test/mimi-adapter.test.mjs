import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const ROOT = new URL('../../../', import.meta.url);

async function text(path) {
  return readFile(new URL(path, ROOT), 'utf8');
}

test('Mimi delegates Computer Use registration to the companion API', async () => {
  const source = await text('dsh-plugin-mimi/lib/index.js');
  assert.match(source, /applyComputerUse\(toolsCtx/);
  assert.match(source, /duplicate guard/);
  assert.doesNotMatch(source, /ctx\.tools\.register\(/);
});

test('Mimi and companion dependency versions stay aligned', async () => {
  const mimi = JSON.parse(await text('dsh-plugin-mimi/package.json'));
  const companion = JSON.parse(await text('vendor/dsh-computer-use/package.json'));
  assert.equal(mimi.dependencies['@milkuovo/dsh-computer-use'], companion.version);
  assert.deepEqual(mimi.bundledDependencies, ['@milkuovo/dsh-computer-use']);
});

test('companion bundle declares the public runtime files', async () => {
  const manifest = JSON.parse(await text('vendor/dsh-computer-use/package.json'));
  assert.deepEqual(manifest.files, ['lib/', 'mcp-server.mjs', 'cordis.patch.yml', 'README.md']);
  assert.equal(manifest.main, 'lib/index.js');
  assert.equal(manifest.dsh.bundle.patch, './cordis.patch.yml');
});
