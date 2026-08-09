const fs = require('fs');
const os = require('os');
const path = require('path');
const asar = require('@electron/asar');

const packages = [
  ['owner', 'D:/Project World/Project-IDEA/IDEA Assistant SRH/resources/app.asar'],
  ['assistant', 'D:/Project World/Project-IDEA/IDEA Assistant/resources/app.asar'],
];
const results = {};

for (const [flavor, archive] of packages) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'idea-model-check-'));
  try {
    asar.extractAll(archive, directory);
    const html = fs.readFileSync(path.join(directory, 'dist', 'index.html'), 'utf8');
    const script = html.match(/assets\/(index-[^"]+\.js)/)?.[1];
    const app = fs.readFileSync(path.join(directory, 'dist', 'assets', script), 'utf8');
    const main = fs.readFileSync(path.join(directory, 'dist-electron', 'main.js'), 'utf8');
    const clientFlavor = JSON.parse(fs.readFileSync(path.join(directory, 'dist-electron', 'client-flavor.json'), 'utf8'));
    results[flavor] = {
      flavorMatches: clientFlavor.flavor === flavor,
      modelSelector: app.includes('DeepSeek V4 Flash') && app.includes('⚡'),
      modelRequest: main.includes('model_key: request.modelKey'),
    };
  } finally {
    fs.rmSync(directory, { recursive: true, force: true });
  }
}

console.log(JSON.stringify(results));
if (Object.values(results).some((item) => Object.values(item).some((value) => !value))) process.exit(1);
