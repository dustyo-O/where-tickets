import React, { useEffect, useState } from 'react';
import { ActivityIndicator, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { fetchHealth, type HealthResult } from '../api/client';

type ScreenState =
  | { kind: 'checking' }
  | { kind: 'ok' }
  | { kind: 'degraded'; failedLink: 'database' | 'backend'; hint: string };

function toScreenState(result: HealthResult): ScreenState {
  return result;
}

function SystemStatusScreen(): React.JSX.Element {
  const [state, setState] = useState<ScreenState>({ kind: 'checking' });

  useEffect(() => {
    let cancelled = false;
    fetchHealth()
      .then(result => {
        if (!cancelled) {
          setState(toScreenState(result));
        }
      })
      .catch(() => {
        if (!cancelled) {
          setState({
            kind: 'degraded',
            failedLink: 'backend',
            hint: 'Is the backend running? Try `just dev`.',
          });
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <SafeAreaView style={styles.safeArea}>
      <View style={styles.container}>{renderContent(state)}</View>
    </SafeAreaView>
  );
}

function renderContent(state: ScreenState): React.JSX.Element {
  switch (state.kind) {
    case 'checking':
      return (
        <>
          <ActivityIndicator size="large" color="#555" />
          <Text style={styles.subtitle}>Checking…</Text>
        </>
      );
    case 'ok':
      return (
        <>
          <Text style={[styles.status, styles.statusOk]}>All systems OK</Text>
          <Text style={styles.subtitle}>
            Backend, database, and app are wired.
          </Text>
        </>
      );
    case 'degraded':
      return (
        <>
          <Text style={[styles.status, styles.statusDegraded]}>
            {`Degraded — ${state.failedLink} down`}
          </Text>
          <Text style={styles.subtitle}>{state.hint}</Text>
        </>
      );
  }
}

const styles = StyleSheet.create({
  safeArea: {
    flex: 1,
  },
  container: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 24,
  },
  status: {
    fontSize: 28,
    fontWeight: '700',
    textAlign: 'center',
  },
  statusOk: {
    color: '#1B8A3A',
  },
  statusDegraded: {
    color: '#C0392B',
  },
  subtitle: {
    marginTop: 12,
    fontSize: 14,
    color: '#555',
    textAlign: 'center',
  },
});

export default SystemStatusScreen;
