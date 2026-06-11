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

async function main() {
  // 1. Full-color logo icons
  const svg = readFileSync(svgPath, 'utf-8')

  // Electron resources
  const resourcesDir = resolve(root, 'resources')
  mkdirSync(resourcesDir, { recursive: true })
  await sharp(Buffer.from(svg)).resize(512, 512).png().toFile(resolve(resourcesDir, 'icon.png'))
  console.log('Created resources/icon.png (512x512)')

  // Electron .ico (Windows) - sharp can't write ICO directly, use 256x256 PNG as base
  await sharp(Buffer.from(svg)).resize(256, 256).png().toFile(resolve(resourcesDir, 'icon-256.png'))
  console.log('Created resources/icon-256.png')

  // Favicon
  const publicDir = resolve(root, 'src', 'renderer', 'public')
  mkdirSync(publicDir, { recursive: true })
  // Copy SVG as favicon (scales perfectly)
  writeFileSync(resolve(publicDir, 'favicon.svg'), svg)
  console.log('Created public/favicon.svg')

  // Also generate PNG favicons for older browsers
  for (const size of sizes.favicon) {
    await sharp(Buffer.from(svg)).resize(size, size).png().toFile(resolve(publicDir, `favicon-${size}.png`))
  }
  await sharp(Buffer.from(svg)).resize(180, 180).png().toFile(resolve(publicDir, 'apple-touch-icon.png'))
  console.log('Created public favicons')

  // 2. Generate white microphone for tray (no background)
  // Extract just the microphone path from the SVG
  const micSvg = `<svg width="256" height="256" viewBox="0 0 256 256" xmlns="http://www.w3.org/2000/svg"><path d="M129.993 107.28C138.776 108.797 152.341 111.386 159.4 115.937C166.459 120.487 171.196 131.585 170.569 143.717C170.412 148.266 169.628 152.664 168.687 155.394C164.138 167.829 150.65 181.629 130.418 181.629C108.46 181.629 95.5994 168.739 94.1878 153.574C93.7173 148.721 94.3447 145.233 95.2857 139.774H124.144C129.634 140.229 131.986 131.585 126.497 130.978H97.4815L99.5204 120.666H128.222C133.868 121.424 136.378 111.87 130.261 111.415H101.246L103.285 101.103H131.986C137.789 101.103 139.515 91.5489 133.241 91.5489H105.167L107.206 81.0851H135.907C140.612 81.0851 143.592 73.0476 137.632 72.5927H108.931L110.97 62.5838H139.828C145.474 62.8871 147.357 53.7881 141.397 53.6364H112.852C114.106 39.3813 129.947 21.1833 153.003 21.3349C170.726 21.3349 184.841 31.4955 189.39 42.7176C190.958 46.3572 191.428 51.3617 190.958 55.9112C190.487 63.1904 186.253 75.3224 179.352 83.9664C170.255 95.4918 154.303 102.882 129.993 107.28Z" fill="white"/><path d="M152.764 106.927C177.396 104.736 192.19 90.5306 200.945 54.7706C197.888 76.2947 195.911 102.524 234.482 105.99C208.441 107.419 191.164 114.185 178.473 154.026C181.682 134.019 183.923 113.437 152.764 106.927Z" fill="white"/></svg>`

  // Generate tray icon PNGs (white microphone on transparent)
  const trayDir = resolve(projectRoot, 'voice_typer', 'server', 'assets')
  mkdirSync(trayDir, { recursive: true })
  for (const size of sizes.tray) {
    await sharp(Buffer.from(micSvg)).resize(size, size).png().toFile(resolve(trayDir, `tray-mic-${size}.png`))
  }
  // Also save the largest as default name
  await sharp(Buffer.from(micSvg)).resize(64, 64).png().toFile(resolve(trayDir, 'tray-mic.png'))
  console.log('Created server/assets/tray-mic-*.png')

  // 3. Full-color logo PNGs for platform.py and ui/app.py
  for (const size of [64, 256]) {
    await sharp(Buffer.from(svg)).resize(size, size).png().toFile(resolve(trayDir, `logo-${size}.png`))
  }
  console.log('Created server/assets/logo-*.png')

  // 4. Generate .ico from 256px PNG using Python PIL
  const { execSync } = await import('child_process')
  const venvPython = resolve(os.homedir(), '.voice-typer', 'venv', 'Scripts', 'python.exe')
  const icoScript = `
from PIL import Image
img = Image.open("${resolve(resourcesDir, 'icon-256.png').replace(/\\/g, '/')}")
img.save("${resolve(resourcesDir, 'icon.ico').replace(/\\/g, '/')}", format="ICO", sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
print("ICO generated")
`
  execSync(`"${venvPython}" -c "${icoScript.replace(/"/g, '\\"')}"`, { stdio: 'pipe' })
  console.log('Created resources/icon.ico')

  console.log('\nAll icons generated successfully.')
}

main().catch(console.error)
