#!/usr/bin/env node
/**
 * mimi-desktop-pet 安装脚本（零依赖，Node 18+）。
 *
 * 把插件装入 DSH profile（目录模式，不依赖 npm publish）：
 *   1. 复制插件到 <profile>/node_modules/mimi-desktop-pet
 *   2. 把插件 insert 写进 <profile>/cordis.patch.yml（用户层 patch，DSH 启动必读）
 *   3. 把 "mimi-desktop-pet" 追加进 profile package.json 的 bundles
 *
 * 用法：
 *   node install.mjs                          # 安装到全部 profile
 *   node install.mjs --profile desktop        # 只装 desktop
 *   node install.mjs --pet-dir "C:\\...\\桌宠"  # 指定桌宠项目目录
 *   node install.mjs --computer-use-dir "C:\\...\\dsh-computer-use"
 */
import { cpSync, existsSync, readdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { join, resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { homedir } from 'node:os';

const PLUGIN_DIR = resolve(dirname(fileURLToPath(import.meta.url)));
const PKG = 'mimi-desktop-pet';

const argv = process.argv.slice(2);
const flagValue = (name) => {
  const i = argv.indexOf(name);
  return i >= 0 && argv[i + 1] ? argv[i + 1] : undefined;
};

const dshHome = process.env.DSH_HOME || join(homedir(), '.dsh');
const petDir = flagValue('--pet-dir') || join(homedir(), 'Desktop', '桌宠');
const onlyProfile = flagValue('--profile');
const computerUseDir = flagValue('--computer-use-dir') || [
  join(PLUGIN_DIR, 'node_modules', '@milkuovo', 'dsh-computer-use'),
  join(PLUGIN_DIR, '..', 'vendor', 'dsh-computer-use'),
  join(homedir(), 'Desktop', 'computer use', 'dsh-computer-use'),
].find((candidate) => existsSync(join(candidate, 'package.json')));

if (!computerUseDir) {
  console.error('未找到 dsh-computer-use；请传入 --computer-use-dir。');
  process.exit(1);
}

function findPython() {
  const localAppData = process.env.LOCALAPPDATA || '';
  const candidates = [
    join(localAppData, 'Programs', 'Python', 'Python311', 'pythonw.exe'),
    join(localAppData, 'Programs', 'Python', 'pythonw.exe'),
  ];
  return candidates.find((c) => existsSync(c)) || 'pythonw';
}

const python = findPython();

const INSERT_BLOCK = [
  '# mimi-desktop-pet 桌宠插件（install.mjs 生成，重启 dsh 生效）',
  '- insert:',
  '    - id: mimi-pet',
  "      name: 'mimi-desktop-pet'",
  '      config:',
  `        petDir: '${petDir}'`,
  `        python: '${python}'`,
  '',
].join('\n');

if (!existsSync(dshHome)) {
  console.error('未找到 DSH home：' + dshHome);
  process.exit(1);
}
const profilesDir = join(dshHome, 'profiles');
const profileNames = onlyProfile
  ? [onlyProfile]
  : existsSync(profilesDir)
    ? readdirSync(profilesDir).filter((n) => existsSync(join(profilesDir, n, 'package.json')))
    : [];

if (profileNames.length === 0) {
  console.error('未找到任何 DSH profile：' + profilesDir);
  process.exit(1);
}

for (const name of profileNames) {
  const profileDir = join(profilesDir, name);

  // 1. copy plugin into profile node_modules (no npm install needed).
  const dest = join(profileDir, 'node_modules', PKG);
  rmSync(dest, { recursive: true, force: true });
  cpSync(PLUGIN_DIR, dest, {
    recursive: true,
    filter: (src) => !src.includes('install.mjs'),
  });
  const computerUseDest = join(dest, 'node_modules', '@milkuovo', 'dsh-computer-use');
  rmSync(computerUseDest, { recursive: true, force: true });
  cpSync(computerUseDir, computerUseDest, {
    recursive: true,
    filter: (src) => !src.includes('.git') && !src.includes('node_modules'),
  });
  // Directory mode: THIS DSH only applies the package's own bundle patch
  // (package.json dsh.bundle.patch) — a profile-level cordis.patch.yml is NOT
  // read at boot (verified 2026-08-22: a blanked package patch means the
  // plugin never loads). So write the insert, with the local petDir/python,
  // INTO the copied package patch, and leave the profile patch stock.
  writeFileSync(join(dest, 'cordis.patch.yml'), INSERT_BLOCK, 'utf8');

  // 2. reset the profile patch to stock if it still carries an old insert —
  //    a duplicate id across both patches would conflict.
  const profilePatch = join(profileDir, 'cordis.patch.yml');
  const existing = existsSync(profilePatch) ? readFileSync(profilePatch, 'utf8') : '';
  if (existing.includes('mimi-pet')) {
    writeFileSync(
      profilePatch,
      '# Your patch layer for this dsh profile (restored by uninstall.mjs)\n[]\n',
      'utf8',
    );
    console.log(`✔ profile patch 已还原为空：${name}`);
  }

  // 3. register the bundle so cordis can resolve the plugin by name.
  const pkgPath = join(profileDir, 'package.json');
  const pkg = JSON.parse(readFileSync(pkgPath, 'utf8'));
  pkg.dsh = pkg.dsh || {};
  pkg.dsh.profile = pkg.dsh.profile || {};
  pkg.dsh.profile.bundles = pkg.dsh.profile.bundles || [];
  if (!pkg.dsh.profile.bundles.includes(PKG)) pkg.dsh.profile.bundles.push(PKG);
  writeFileSync(pkgPath, JSON.stringify(pkg, null, 2) + '\n', 'utf8');

  console.log(`✔ 已安装到 profile "${name}"`);
  console.log(`  petDir = ${petDir}`);
  console.log(`  python = ${python}`);
  console.log(`  computerUse = ${computerUseDir}`);
}

console.log('\n重启 dsh 后生效（桌宠会随 DSH 自动启动）。');
