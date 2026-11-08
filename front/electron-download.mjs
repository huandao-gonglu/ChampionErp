import checksums from 'electron/checksums.json' with { type: 'json' }

export default {
  electronDownload: {
    // 显式设置 force，让 electron-builder 26 按 @electron/get 配置读取 checksums。
    force: false,
    checksums,
  },
}
