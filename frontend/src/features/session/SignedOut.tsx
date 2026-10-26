type OwnProps = { rejected: boolean };

export const SignedOut = ({ rejected }: OwnProps) => (
  <main className="mx-auto flex min-h-dvh max-w-xl items-center px-6 py-12">
    <div className="w-full border-l-2 border-sand-6 pl-6">
      <h1 className="mb-4 text-2xl font-semibold tracking-tight text-sand-12">Tasks</h1>
      {rejected ? (
        <>
          <p className="mb-2 text-lg text-sand-12">This link no longer works.</p>
          <p className="leading-relaxed text-sand-11">Send /dashboard to the bot for a new link</p>
        </>
      ) : (
        <p className="leading-relaxed text-sand-11">
          Open your dashboard from the bot: send /dashboard to get a link.
        </p>
      )}
    </div>
  </main>
);
