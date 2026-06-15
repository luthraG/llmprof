// @ts-check
import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';

// Project site on GitHub Pages: https://luthrag.github.io/llmprof
// https://astro.build/config
export default defineConfig({
	site: 'https://luthrag.github.io',
	base: '/llmprof',
	integrations: [
		starlight({
			title: 'llmprof',
			description: 'pprof for your LLM context. See where every token and dollar goes.',
			logo: { src: './src/assets/logo.svg', replacesTitle: false },
			favicon: '/favicon.svg',
			// Social card. og.png lives in public/ so it has a stable absolute URL.
			head: [
				{ tag: 'meta', attrs: { property: 'og:image', content: 'https://luthrag.github.io/llmprof/og.png' } },
				{ tag: 'meta', attrs: { property: 'og:image:width', content: '1200' } },
				{ tag: 'meta', attrs: { property: 'og:image:height', content: '630' } },
				{ tag: 'meta', attrs: { name: 'twitter:card', content: 'summary_large_image' } },
				{ tag: 'meta', attrs: { name: 'twitter:image', content: 'https://luthrag.github.io/llmprof/og.png' } },
			],
			customCss: ['./src/styles/theme.css'],
			social: [
				{ icon: 'github', label: 'GitHub', href: 'https://github.com/luthraG/llmprof' },
			],
			sidebar: [
				{
					label: 'Get started',
					items: [
						{ label: 'Overview', slug: 'index' },
						{ label: 'Installation', slug: 'start/installation' },
						{ label: 'Quickstart', slug: 'start/quickstart' },
					],
				},
				{
					label: 'Concepts',
					items: [
						{ label: 'Architecture', slug: 'concepts/architecture' },
						{ label: 'The waste detector', slug: 'concepts/waste-detector' },
					],
				},
				{
					label: 'Features',
					items: [
						{ label: 'Context flame graph', slug: 'features/flame-graph' },
						{ label: 'Trends', slug: 'features/trends' },
						{ label: 'Context timeline', slug: 'features/timeline' },
						{ label: 'Cost leaderboard', slug: 'features/leaderboard' },
					],
				},
				{
					label: 'Integrations',
					items: [
						{ label: 'OpenAI-compatible clients', slug: 'integrations/openai' },
						{ label: 'Anthropic', slug: 'integrations/anthropic' },
						{ label: 'Claude Code', slug: 'integrations/claude-code' },
						{ label: 'Codex CLI', slug: 'integrations/codex' },
					],
				},
				{
					label: 'SDK',
					items: [
						{ label: 'Python SDK', slug: 'sdk/python' },
						{ label: 'JavaScript / TypeScript SDK', slug: 'sdk/javascript' },
					],
				},
				{
					label: 'Reference',
					items: [
						{ label: 'Providers & pricing', slug: 'reference/pricing' },
						{ label: 'Storage backends', slug: 'reference/storage' },
						{ label: 'Configuration', slug: 'reference/configuration' },
						{ label: 'CLI', slug: 'reference/cli' },
					],
				},
				{
					label: 'Project',
					items: [
						{ label: 'Roadmap', slug: 'project/roadmap' },
						{ label: 'Contributing', slug: 'project/contributing' },
					],
				},
			],
		}),
	],
});
