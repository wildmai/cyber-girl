const path = require('node:path');
const { packager } = require('@electron/packager');

packager({ dir: __dirname, out: path.join(__dirname, '..', 'output', 'desktop'),
  name: 'Xiaoman', executableName: 'Xiaoman', platform: 'win32', arch: 'x64',
  electronVersion: require('./package.json').devDependencies.electron,
  icon: path.join(__dirname, 'assets', 'icon.ico'), overwrite: true, asar: true,
  ignore: [/\.test\.cjs$/, /build\.cjs$/, /package-lock\.json$/],
  appCopyright: '小满', win32metadata: { ProductName: '小满 · 赛博女友', FileDescription: '小满 AI 陪伴桌面版' },
}).then(paths => paths.forEach(file => console.log(file)))
  .catch(error => { console.error(error.message); process.exitCode = 1; });
