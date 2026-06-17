import sharp from 'sharp'
import { writeFileSync, mkdirSync, readFileSync } from 'fs'
import os from 'os'
import { resolve, dirname } from 'path'
import { fileURLToPath } from 'url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const root = resolve(__dirname, '..')
const projectRoot = resolve(__dirname, '..', '..', '..')
const svgPath = resolve(projectRoot, 'voice_typer', 'vt_logo.svg')

const sizes = {
  favicon: [16, 32, 48],
  electron: [512],
  ico: [16, 24, 32, 48, 64, 128, 256],
  tray: [16, 24, 32, 48, 64],
}

async function generateIcons(svg, label, suffix) {
  const resourcesDir = resolve(root, 'resources')
  const publicDir = resolve(root, 'src', 'renderer', 'public')

  // Electron resources
  await sharp(Buffer.from(svg)).resize(512, 512).png().toFile(resolve(resourcesDir, `icon${suffix}.png`))
  console.log(`Created resources/icon${suffix}.png (512x512) [${label}]`)

  await sharp(Buffer.from(svg)).resize(256, 256).png().toFile(resolve(resourcesDir, `icon${suffix}-256.png`))
  console.log(`Created resources/icon${suffix}-256.png [${label}]`)

  // PNG favicons
  for (const size of sizes.favicon) {
    await sharp(Buffer.from(svg)).resize(size, size).png().toFile(resolve(publicDir, `favicon${suffix}-${size}.png`))
  }
  if (!suffix) {
    writeFileSync(resolve(publicDir, 'favicon.svg'), svg)
    console.log('Created public/favicon.svg')
    await sharp(Buffer.from(svg)).resize(180, 180).png().toFile(resolve(publicDir, 'apple-touch-icon.png'))
  }
  console.log(`Created public favicons${suffix && ' (dark)'}`)
}

async function generateIco(pngPath, icoPath) {
  const { execSync } = await import('child_process')
  const venvPython = resolve(os.homedir(), '.voice-typer', 'venv', 'Scripts', 'python.exe')
  const icoScript = `
from PIL import Image
img = Image.open("${pngPath.replace(/\\/g, '/')}")
img.save("${icoPath.replace(/\\/g, '/')}", format="ICO", sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
print("ICO generated")
`
  execSync(`"${venvPython}" -c "${icoScript.replace(/"/g, '\\"')}"`, { stdio: 'pipe' })
}

async function main() {
  const lightSvg = readFileSync(svgPath, 'utf-8')
  const darkSvg = lightSvg.replace(/fill="black"/g, 'fill="white"')

  // Light icons (black logo on transparent)
  await generateIcons(lightSvg, 'light', '')
  // Dark icons (white logo on transparent)
  await generateIcons(darkSvg, 'dark', '-dark')

  // .ico generation
  const resourcesDir = resolve(root, 'resources')
  await generateIco(
    resolve(resourcesDir, 'icon-256.png'),
    resolve(resourcesDir, 'icon.ico')
  )
  await generateIco(
    resolve(resourcesDir, 'icon-dark-256.png'),
    resolve(resourcesDir, 'icon-dark.ico')
  )

  // Tray icons (white elements on transparent, used by Python colorization)
  const micSvg = `<svg width="128" height="109" viewBox="0 0 128 109" xmlns="http://www.w3.org/2000/svg"><rect width="13.5631" height="108.504" rx="6.78154" fill="white"/><path d="M77.0728 3.9668C71.5231 34.1984 57.8119 48.3888 27.125 55.7925C51.6092 61.0367 69.2379 69.3659 77.0728 104.842C84.2548 72.4507 97.6396 63.1961 128 55.7925C99.2718 49.3143 83.9284 36.9748 77.0728 3.9668Z" fill="white"/></svg>`

  const trayDir = resolve(projectRoot, 'voice_typer', 'server', 'assets')
  mkdirSync(trayDir, { recursive: true })
  for (const size of sizes.tray) {
    await sharp(Buffer.from(micSvg)).resize(size, size).png().toFile(resolve(trayDir, `tray-mic-${size}.png`))
  }
  await sharp(Buffer.from(micSvg)).resize(64, 64).png().toFile(resolve(trayDir, 'tray-mic.png'))
  console.log('Created server/assets/tray-mic-*.png')

  // Logo PNGs for Python server
  for (const size of [64, 256]) {
    await sharp(Buffer.from(lightSvg)).resize(size, size).png().toFile(resolve(trayDir, `logo-${size}.png`))
  }
  console.log('Created server/assets/logo-*.png')

  console.log('\nAll icons generated successfully.')
}

main().catch(console.error)
