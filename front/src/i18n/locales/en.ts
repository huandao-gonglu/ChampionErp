export default {
  app: {
    title: 'Champion ERP Core Workflow',
    subtitle: 'Frontend workflow board powered by backend APIs',
  },
  routes: {
    workflow: {
      title: 'Cross-border ERP Workbench',
      description: 'Collect, library, editing, copy, pricing, precheck, auth, and logs.',
    },
    notFound: {
      title: 'Page not found',
      description: 'The requested page does not exist or has moved.',
    },
  },
  nav: {
    dashboard: { title: 'Dashboard', subtitle: 'Workflow overview' },
    collect: { title: 'Collect', subtitle: 'Links, cookies, browser tabs' },
    library: { title: 'Library', subtitle: 'Local product master' },
    drafts: { title: 'Drafts', subtitle: 'Platform drafts, continue editing' },
    publish: { title: 'Publish Queue', subtitle: 'Jobs and logs' },
    onlineProducts: { title: 'Online products', subtitle: 'Store products, prices and stock' },
    auth: { title: 'Platform Auth', subtitle: 'Auth, AI, exchange rates' },
    logs: { title: 'Publish Logs', subtitle: 'Requests, responses, errors' },
  },
  pages: {
    drafts: {
      title: 'Drafts',
      description: 'Platform editing drafts created from the product library, focused on copy, images, category, and publish precheck.',
    },
    publish: {
      title: 'Publish Queue',
      description: 'Publishing jobs, task status, and run logs.',
    },
    onlineProducts: {
      title: 'Online products',
      description: 'Sync and manage existing store products, prices, stock and content.',
    },
    logs: {
      title: 'Publish Logs',
      description: 'Publishing requests, responses, error codes, and next actions.',
    },
  },
  settings: {
    uiLanguage: 'UI language',
  },
}
