import { test, expect } from '@playwright/experimental-ct-react'
import App from './App' // or wherever your root/component lives

test('renders the app', async ({ mount }) => {
  const component = await mount(<App />)
  await expect(component).toContainText(/react/i)
})
