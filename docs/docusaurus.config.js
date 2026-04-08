// @ts-check
const { themes } = require('prism-react-renderer');

/** @type {import('@docusaurus/types').Config} */
const config = {
  title: 'Tinker',
  tagline: 'AI-powered observability and incident response agent',
  favicon: 'img/favicon.ico',

  url: 'https://gettinker.github.io',
  baseUrl: '/tinkr/',

  organizationName: 'gettinker',
  projectName: 'tinkr',
  trailingSlash: false,

  onBrokenLinks: 'throw',
  onBrokenMarkdownLinks: 'warn',

  i18n: {
    defaultLocale: 'en',
    locales: ['en'],
  },

  presets: [
    [
      'classic',
      /** @type {import('@docusaurus/preset-classic').Options} */
      ({
        docs: {
          sidebarPath: require.resolve('./sidebars.js'),
          editUrl: 'https://github.com/gettinker/tinkr/tree/main/docs/',
          routeBasePath: '/',
        },
        blog: false,
        theme: {
          customCss: require.resolve('./src/css/custom.css'),
        },
      }),
    ],
  ],

  themeConfig:
    /** @type {import('@docusaurus/preset-classic').ThemeConfig} */
    ({
      image: 'img/tinker-social.png',
      navbar: {
        title: 'Tinker',
        logo: {
          alt: 'Tinker Logo',
          src: 'img/logo.svg',
        },
        items: [
          {
            type: 'docSidebar',
            sidebarId: 'docs',
            position: 'left',
            label: 'Docs',
          },
          {
            to: '/commands',
            label: 'Commands',
            position: 'left',
          },
          {
            to: '/backends',
            label: 'Backends',
            position: 'left',
          },
          {
            href: 'https://github.com/gettinker/tinkr',
            label: 'GitHub',
            position: 'right',
          },
        ],
      },
      footer: {
        style: 'dark',
        links: [
          {
            title: 'Docs',
            items: [
              { label: 'Quick Start', to: '/' },
              { label: 'Installation', to: '/install' },
              { label: 'Commands', to: '/commands' },
              { label: 'Backends', to: '/backends' },
            ],
          },
          {
            title: 'Integrations',
            items: [
              { label: 'GitHub', to: '/integrations/github' },
              { label: 'Slack', to: '/integrations/slack' },
              { label: 'Webhooks', to: '/integrations/webhooks' },
            ],
          },
          {
            title: 'Deploy',
            items: [
              { label: 'AWS', to: '/deployment/aws' },
              { label: 'GCP', to: '/deployment/gcp' },
              { label: 'Azure', to: '/deployment/azure' },
              { label: 'Docker / Self-hosted', to: '/deployment/docker' },
            ],
          },
          {
            title: 'Community',
            items: [
              {
                label: 'GitHub',
                href: 'https://github.com/gettinker/tinkr',
              },
              {
                label: 'Issues',
                href: 'https://github.com/gettinker/tinkr/issues',
              },
            ],
          },
        ],
        copyright: `Copyright © ${new Date().getFullYear()} Tinker. Built with Docusaurus.`,
      },
      prism: {
        theme: themes.github,
        darkTheme: themes.dracula,
        additionalLanguages: ['bash', 'toml', 'json', 'yaml', 'python'],
      },
      colorMode: {
        defaultMode: 'dark',
        disableSwitch: false,
        respectPrefersColorScheme: true,
      },
      algolia: undefined,
    }),
};

module.exports = config;
