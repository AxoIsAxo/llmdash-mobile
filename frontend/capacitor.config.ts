import type { CapacitorConfig } from '@capacitor/cli'

const config: CapacitorConfig = {
  appId: 'eu.redforged.llmdash',
  appName: 'LLMDash',
  webDir: 'dist',
  android: {
    backgroundColor: '#030712',
    allowMixedContent: false,
  },
  plugins: {
    SplashScreen: {
      backgroundColor: '#030712',
      showSpinner: false,
      androidSplashResourceName: 'splash',
      launchAutoHide: true,
      launchShowDuration: 800,
    },
    StatusBar: {
      style: 'DARK',
      backgroundColor: '#030712',
    },
  },
}

export default config
