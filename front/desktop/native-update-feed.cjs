const http = require('node:http')
const { createReadStream } = require('node:fs')
const { randomBytes } = require('node:crypto')

// 原生更新器只接收已通过大小及摘要校验的本机 ZIP，不再次信任远端下载地址。
async function createNativeUpdateFeed(file, asset, version) {
  const token = randomBytes(24).toString('hex')
  let base
  const server = http.createServer((request, response) => {
    if (request.method !== 'GET') { response.writeHead(405).end(); return }
    if (request.url === `/${token}/feed`) {
      const data = JSON.stringify({ url: `${base}/${token}/update.zip`, name: version, size: asset.size, sha256: asset.sha256 })
      response.writeHead(200, { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(data), 'Cache-Control': 'no-store' })
      response.end(data)
    } else if (request.url === `/${token}/update.zip`) {
      response.writeHead(200, { 'Content-Type': 'application/zip', 'Content-Length': asset.size, 'Cache-Control': 'no-store' })
      const stream = createReadStream(file)
      stream.on('error', error => response.destroy(error))
      response.on('close', () => stream.destroy())
      stream.pipe(response)
    } else response.writeHead(404).end()
  })
  await new Promise((resolve, reject) => {
    server.once('error', reject)
    server.listen(0, '127.0.0.1', resolve)
  })
  base = `http://127.0.0.1:${server.address().port}`
  return {
    url: `${base}/${token}/feed`,
    close: () => new Promise((resolve, reject) => {
      server.close(error => error ? reject(error) : resolve())
      server.closeAllConnections()
    }),
  }
}

module.exports = { createNativeUpdateFeed }
