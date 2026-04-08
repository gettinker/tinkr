/** @type {import('@docusaurus/plugin-content-docs').SidebarsConfig} */
const sidebars = {
  docs: [
    {
      type: 'doc',
      id: 'index',
      label: 'Quick Start',
    },
    {
      type: 'doc',
      id: 'install',
      label: 'Installation',
    },
    {
      type: 'category',
      label: 'Backends',
      collapsed: false,
      items: [
        'backends/index',
        'backends/cloudwatch',
        'backends/gcp',
        'backends/azure',
        'backends/grafana',
        'backends/datadog',
        'backends/elastic',
        'backends/otel',
      ],
    },
    {
      type: 'category',
      label: 'Deployment',
      collapsed: false,
      items: [
        'deployment/docker',
        'deployment/aws',
        'deployment/gcp',
        'deployment/azure',
      ],
    },
    {
      type: 'category',
      label: 'Integrations',
      collapsed: false,
      items: [
        'integrations/github',
        'integrations/slack',
        'integrations/webhooks',
      ],
    },
    {
      type: 'doc',
      id: 'configuration',
      label: 'Configuration Reference',
    },
    {
      type: 'category',
      label: 'Commands',
      collapsed: false,
      items: [
        'commands/index',
        'commands/logs',
        'commands/tail',
        'commands/metrics',
        'commands/anomaly',
        'commands/trace',
        'commands/diff',
        'commands/investigate',
        'commands/rca',
        'commands/slo',
        'commands/watch',
        'commands/alert',
        'commands/deploy',
        'commands/profile',
      ],
    },
    {
      type: 'doc',
      id: 'security',
      label: 'Security',
    },
  ],
};

module.exports = sidebars;
