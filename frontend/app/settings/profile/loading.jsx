import React from 'react';
import ShellSkeleton from '../../components/ShellSkeleton';

export default function Loading() {
  return <ShellSkeleton label="Loading your profile" panels={2} />;
}
