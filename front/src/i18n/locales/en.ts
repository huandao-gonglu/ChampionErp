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
    pricing: { title: 'Pricing', subtitle: 'Cost, freight, margin' },
    category: { title: 'Publish Precheck', subtitle: 'Category, attributes, payload' },
    publish: { title: 'Publish Queue', subtitle: 'Jobs and logs' },
    mlItems: { title: 'ML Published', subtitle: 'Remote items, close listing' },
    pending: { title: 'Pending', subtitle: 'Incomplete or failed items' },
    auth: { title: 'Platform Auth', subtitle: 'Auth, AI, exchange rates' },
    logs: { title: 'Publish Logs', subtitle: 'Requests, responses, errors' },
  },
  pages: {
    drafts: {
      title: 'Drafts',
      description: 'Platform editing drafts created from the product library, focused on copy, images, category, and publish precheck.',
    },
    pricing: {
      title: 'Pricing',
      description: 'Calculate cost, freight, commission, exchange rates, and margin.',
    },
    precheck: {
      title: 'Publish Precheck',
      description: 'Search categories, fill required attributes, preview payloads, and validate before publishing.',
    },
    publish: {
      title: 'Publish Queue',
      description: 'Publishing jobs, task status, and run logs.',
    },
    mlItems: {
      title: 'ML Published Items',
      description: 'Review Mercado Libre account listings in real time and close listings via API.',
    },
    pending: {
      title: 'Pending',
      description: 'Items still pending, failed, not ready, or partially complete across collect, copy, images, category, precheck, or publish.',
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
