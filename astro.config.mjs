import { defineConfig } from 'astro/config';
import { unified } from '@astrojs/markdown-remark';
import starlight from '@astrojs/starlight';
import remarkMermaid from './src/plugins/remark-mermaid.mjs';

const repository = process.env.GITHUB_REPOSITORY ?? '';

export default defineConfig({
  site: 'https://wsafight.github.io',
  base: '/polars-docs',
  markdown: {
    processor: unified({ remarkPlugins: [remarkMermaid] }),
  },
  integrations: [
    starlight({
      title: 'Polars 深度教程',
      description: '从心智模型到生产级 Lazy ETL，系统掌握地道 Polars。',
      logo: {
        src: './src/assets/polars-mark.svg',
        alt: 'Polars 深度教程',
      },
      favicon: '/favicon.svg',
      locales: {
        root: {
          label: '简体中文',
          lang: 'zh-CN',
        },
      },
      customCss: ['./src/styles/site.css'],
      components: {
        Head: './src/components/Head.astro',
      },
      pagefind: true,
      lastUpdated: true,
      tableOfContents: {
        minHeadingLevel: 2,
        maxHeadingLevel: 3,
      },
      ...(repository
        ? {
            editLink: {
              baseUrl: `https://github.com/${repository}/edit/main/`,
            },
          }
        : {}),
      sidebar: [
        {
          label: '开始',
          items: [{ slug: 'index', label: '00 · 心智模型总纲' }],
        },
        {
          label: '地基层',
          items: [
            { slug: '01-data-structures' },
            { slug: '02-expressions' },
            { slug: '03-contexts' },
          ],
        },
        {
          label: '引擎层',
          items: [{ slug: '04-lazy-optimizer' }],
        },
        {
          label: '操作层',
          items: [
            { slug: '05-aggregation' },
            { slug: '06-joins' },
            { slug: '07-reshape' },
            { slug: '08-complex-types' },
            { slug: '09-time-series' },
          ],
        },
        {
          label: '生产层',
          items: [
            { slug: '10-io-streaming' },
            { slug: '11-performance' },
            { slug: '12-migration-sql' },
          ],
        },
        {
          label: '实战层',
          items: [
            { slug: '13-cleaning' },
            { slug: '14-end-to-end' },
            { slug: '15-udf-interop' },
          ],
        },
      ],
    }),
  ],
});
