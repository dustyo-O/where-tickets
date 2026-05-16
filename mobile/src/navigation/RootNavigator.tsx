import React from 'react';
import { NavigationContainer } from '@react-navigation/native';
import { createNativeStackNavigator } from '@react-navigation/native-stack';

import SystemStatusScreen from '../screens/SystemStatusScreen';

export type RootStackParamList = {
  SystemStatus: undefined;
};

const Stack = createNativeStackNavigator<RootStackParamList>();

function RootNavigator(): React.JSX.Element {
  return (
    <NavigationContainer>
      <Stack.Navigator screenOptions={{ headerShown: false }}>
        <Stack.Screen name="SystemStatus" component={SystemStatusScreen} />
      </Stack.Navigator>
    </NavigationContainer>
  );
}

export default RootNavigator;
