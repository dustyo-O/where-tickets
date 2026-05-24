declare module 'react-native-config' {
  interface Env {
    BACKEND_URL?: string;
  }

  const Config: Env;
  export default Config;
}
