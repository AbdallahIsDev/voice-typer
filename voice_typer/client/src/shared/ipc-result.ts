export type IpcResult<T = Record<string, never>> =
	| ({ success: true } & T)
	| { success: false; error?: string };

export async function withIpcEnvelope<
	TReturn extends IpcResult<Record<string, unknown>>,
>(
	handler: () => Promise<TReturn> | TReturn,
): Promise<TReturn | { success: false; error: string }> {
	try {
		return await handler();
	} catch (e: unknown) {
		return {
			success: false,
			error: e instanceof Error ? e.message : String(e),
		};
	}
}
