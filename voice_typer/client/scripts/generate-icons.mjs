import sharp from 'sharp'
import { writeFileSync, mkdirSync, readFileSync, existsSync } from 'fs'
import os from 'os'
import { resolve, dirname } from 'path'
import { fileURLToPath } from 'url'

const __dirname = dirname(fileURLToPath(import.meta.url))
// NEW-DEAD-034: renamed ``root`` → ``clientDir`` and kept
// ``projectRoot`` for the repo root.  The old names (``root`` vs
// ``projectRoot``) were confusing — ``root`` sounded like the repo
// root but was actually the client/ directory.
const clientDir = resolve(__dirname, '..')
const projectRoot = resolve(__dirname, '..', '..', '..')
const svgPath = resolve(__dirname, 'logo.svg')

const sizes = {
  favicon: [16, 32, 48],
  electron: [512],
  ico: [16, 24, 32, 48, 64, 128, 256],
  tray: [16, 24, 32, 48, 64],
}

async function generateIcons(svg, label, suffix) {
  const resourcesDir = resolve(clientDir, 'resources')
  const publicDir = resolve(clientDir, 'src', 'renderer', 'public')

  // Electron resources
  await sharp(Buffer.from(svg)).resize(512, 512).png().toFile(resolve(resourcesDir, `icon${suffix}.png`))
  console.log(`Created resources/icon${suffix}.png (512x512) [${label}]`)

  await sharp(Buffer.from(svg)).resize(256, 256).png().toFile(resolve(resourcesDir, `icon${suffix}-256.png`))
  console.log(`Created resources/icon${suffix}-256.png [${label}]`)

  // PNG favicons
  for (const size of sizes.favicon) {
    await sharp(Buffer.from(svg)).resize(size, size).png().toFile(resolve(publicDir, `favicon${suffix}-${size}.png`))
  }    if (!suffix) {
      // Write a theme-aware favicon.svg that uses prefers-color-scheme media query
      // instead of currentColor, which doesn't reliably work in SVG favicons.
      const faviconSvg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128" fill="none">
  <style>
    .bar { fill: #1a1a1a; }
    @media (prefers-color-scheme: dark) {
      .bar { fill: #f0f0f0; }
    }
  </style>
  <rect class="bar" x="15" y="48" width="14" height="32" rx="7"/>
  <rect class="bar" x="43" y="32" width="14" height="64" rx="7"/>
  <rect class="bar" x="71" y="16" width="14" height="96" rx="7"/>
  <rect class="bar" x="99" y="40" width="14" height="48" rx="7"/>
</svg>`
      writeFileSync(resolve(publicDir, 'favicon.svg'), faviconSvg)
      console.log('Created public/favicon.svg (theme-aware)')
      await sharp(Buffer.from(svg)).resize(180, 180).png().toFile(resolve(publicDir, 'apple-touch-icon.png'))
    }
  console.log(`Created public favicons${suffix && ' (dark)'}`)
}

async function generateIco(pngPath, icoPath) {
  const { execSync } = await import('child_process')
  // NEW-DOC-024: previously this hardcoded a venv python path as the
  // FIRST candidate (``~/.voice-typer/venv/Scripts/python.exe``) —
  // a path that almost never exists on a developer's machine (it
  // only exists inside an installed app's bundled venv, not in a
  // source checkout).  The order is now:
  //   1. The current project's venv (``.venv/bin/python`` or
  //      ``.venv/Scripts/python.exe`` relative to the repo root).
  //   2. ``python3`` from PATH (the most common dev environment).
  //   3. ``python`` from PATH (Windows often only has ``python``).
  //   4. The legacy app venv path (only present inside an installed
  //      app, kept as a last-resort fallback for build pipelines
  //      that run inside the installed app's directory).
  // The first one that exists AND can import PIL wins.
  const projectVenvPython = process.platform === 'win32'
    ? resolve(projectRoot, '.venv', 'Scripts', 'python.exe')
    : resolve(projectRoot, '.venv', 'bin', 'python')
  const legacyAppVenvPython = process.platform === 'win32'
    ? resolve(os.homedir(), '.voice-typer', 'venv', 'Scripts', 'python.exe')
    : resolve(os.homedir(), '.voice-typer', 'venv', 'bin', 'python3')
  const candidates = [
    projectVenvPython,
    'python3',
    'python',
    legacyAppVenvPython,
  ]
  const icoScript = `
from PIL import Image
img = Image.open("${pngPath.replace(/\\/g, '/')}")
img.save("${icoPath.replace(/\\/g, '/')}", format="ICO", sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
print("ICO generated")
`
  let lastErr = null
  for (const py of candidates) {
    // Skip venv candidates (paths starting with . or /) that don't
    // exist on this platform — they would just produce a noisy error.
    if ((py.startsWith('.') || py.startsWith('/')) && !existsSync(py)) continue
    try {
      execSync(`"${py}" -c "${icoScript.replace(/"/g, '\\"')}"`, { stdio: 'pipe' })
      return  // success
    } catch (e) {
      lastErr = e
      // Try next candidate
    }
  }
  throw new Error(
    `Failed to generate ICO: no working Python+PIL found. ` +
    `Tried: ${candidates.join(', ')}. ` +
    `Install Pillow with: pip install Pillow. ` +
    `Last error: ${lastErr?.message ?? 'unknown'}`
  )
}

async function main() {
  const rawSvg = readFileSync(svgPath, 'utf-8')
  // The source SVG uses currentColor — replace with explicit colors for rendering
  const lightSvg = rawSvg.replace(/currentColor/g, 'black')
  const darkSvg = rawSvg.replace(/currentColor/g, 'white')

  // Light icons (black logo on transparent)
  await generateIcons(lightSvg, 'light', '')
  // Dark icons (white logo on transparent)
  await generateIcons(darkSvg, 'dark', '-dark')

  // .ico generation
  const resourcesDir = resolve(clientDir, 'resources')
  await generateIco(
    resolve(resourcesDir, 'icon-256.png'),
    resolve(resourcesDir, 'icon.ico')
  )
  await generateIco(
    resolve(resourcesDir, 'icon-dark-256.png'),
    resolve(resourcesDir, 'icon-dark.ico')
  )

  // Tray icons (transparent background, white bars for colorization)
  const traySvg = `<svg width="148" height="148" viewBox="0 0 148 148" fill="none" xmlns="http://www.w3.org/2000/svg"><rect x="18.5" y="55.5" width="18.5" height="37" rx="9.25" fill="white"/><rect x="49.3333" y="37" width="18.5" height="74" rx="9.25" fill="white"/><rect x="80.1667" y="18.5" width="18.5" height="111" rx="9.25" fill="white"/><rect x="111" y="45.0938" width="18.5" height="57.8125" rx="9.25" fill="white"/></svg>`

  const trayDir = resolve(projectRoot, 'voice_typer', 'server', 'assets')
  mkdirSync(trayDir, { recursive: true })
  for (const size of sizes.tray) {
    await sharp(Buffer.from(traySvg)).resize(size, size).png().toFile(resolve(trayDir, `tray-mic-${size}.png`))
  }
  await sharp(Buffer.from(traySvg)).resize(64, 64).png().toFile(resolve(trayDir, 'tray-mic.png'))
  console.log('Created server/assets/tray-mic-*.png')

  // PLAT-024: Generate a multi-size ICO file for the tray icon so
  // Windows 11 can use the native ICO format (sharper than PNG under
  // per-monitor DPI scaling). The ICO contains 16/24/32/48/64 sizes.
  // tray_icon.py looks for ``tray-mic.png`` (the base icon) and
  // colorizes it at runtime per AppState; we generate the ICO from
  // the same base PNG so the runtime conversion path can be skipped
  // on Windows when the base ICO is present.
  await generateIco(
    resolve(trayDir, 'tray-mic.png'),
    resolve(trayDir, 'tray-mic.ico')
  )
  console.log('Created server/assets/tray-mic.ico (PLAT-024)')

  // Logo PNGs for Python server (transparent background)
  for (const size of [64, 256]) {
    await sharp(Buffer.from(lightSvg)).resize(size, size).png().toFile(resolve(trayDir, `logo-${size}.png`))
  }
  console.log('Created server/assets/logo-*.png')

  console.log('\nAll icons generated successfully.')
}

main().catch(console.error)
