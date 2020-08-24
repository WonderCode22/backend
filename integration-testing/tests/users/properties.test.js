const fs = require('fs')
const path = require('path')
const rp = require('request-promise-native')
const uuidv4 = require('uuid/v4')

const cognito = require('../../utils/cognito')
const {mutations, queries} = require('../../schema')

const grantData = fs.readFileSync(path.join(__dirname, '..', '..', 'fixtures', 'grant.jpg'))
const grantDataB64 = new Buffer.from(grantData).toString('base64')
const loginCache = new cognito.AppSyncLoginCache()
jest.retryTimes(2)

beforeAll(async () => {
  loginCache.addCleanLogin(await cognito.getAppSyncLogin())
  loginCache.addCleanLogin(await cognito.getAppSyncLogin())
})

beforeEach(async () => await loginCache.clean())
afterAll(async () => await loginCache.reset())

describe('Read and write properties our our own profile', () => {
  // username is tested in the set-username.test.js

  test('followed/follwer status', async () => {
    const {client, userId} = await loginCache.getCleanLogin()
    let resp = await client.query({query: queries.user, variables: {userId}})
    expect(resp.data.user.followedStatus).toBe('SELF')
    expect(resp.data.user.followerStatus).toBe('SELF')
  })

  test('privacyStatus', async () => {
    const {client, userId} = await loginCache.getCleanLogin()
    let resp = await client.query({query: queries.user, variables: {userId}})
    expect(resp.data.user.privacyStatus).toBe('PUBLIC')

    resp = await client.mutate({mutation: mutations.setUserPrivacyStatus, variables: {privacyStatus: 'PRIVATE'}})
    expect(resp.data.setUserDetails.privacyStatus).toBe('PRIVATE')

    resp = await client.query({query: queries.user, variables: {userId}})
    expect(resp.data.user.privacyStatus).toBe('PRIVATE')

    resp = await client.mutate({mutation: mutations.setUserPrivacyStatus, variables: {privacyStatus: 'PUBLIC'}})
    expect(resp.data.setUserDetails.privacyStatus).toBe('PUBLIC')

    resp = await client.query({query: queries.user, variables: {userId}})
    expect(resp.data.user.privacyStatus).toBe('PUBLIC')
  })

  test('detail info', async () => {
    const bio = "truckin'"
    const fullName = 'Hunter S.'
    const gender = 'Male'
    const birthDate = '1990-01-01'
    const {client, userId} = await loginCache.getCleanLogin()

    let resp = await client.query({query: queries.user, variables: {userId}})
    expect(resp.data.user.bio).toBeNull()
    expect(resp.data.user.fullName).toBeNull()
    expect(resp.data.user.gender).toBeNull()
    expect(resp.data.user.birthDate).toBeNull()

    // set to some custom values
    resp = await client.mutate({
      mutation: mutations.setUserDetails,
      variables: {bio, fullName, gender, birthDate},
    })
    expect(resp.data.setUserDetails.bio).toBe(bio)
    expect(resp.data.setUserDetails.fullName).toBe(fullName)
    expect(resp.data.setUserDetails.gender).toBe(gender)
    expect(resp.data.setUserDetails.birthDate).toBe(birthDate)

    resp = await client.query({query: queries.user, variables: {userId}})
    expect(resp.data.user.bio).toBe(bio)
    expect(resp.data.user.fullName).toBe(fullName)
    expect(resp.data.user.gender).toBe(gender)
    expect(resp.data.user.birthDate).toBe(birthDate)

    // clear out the custom values
    resp = await client.mutate({
      mutation: mutations.setUserDetails,
      variables: {bio: '', fullName: '', gender: '', birthDate: ''},
    })
    expect(resp.data.setUserDetails.bio).toBeNull()
    expect(resp.data.setUserDetails.fullName).toBeNull()
    expect(resp.data.setUserDetails.gender).toBeNull()
    expect(resp.data.setUserDetails.birthDate).toBeNull()

    resp = await client.query({query: queries.user, variables: {userId}})
    expect(resp.data.user.bio).toBeNull()
    expect(resp.data.user.fullName).toBeNull()
    expect(resp.data.user.gender).toBeNull()
    expect(resp.data.user.birthDate).toBeNull()
  })
})

test('Disabled user cannot setUserDetails', async () => {
  const {client, userId} = await loginCache.getCleanLogin()

  // disable ourselves
  let resp = await client.mutate({mutation: mutations.disableUser})
  expect(resp.data.disableUser.userId).toBe(userId)
  expect(resp.data.disableUser.userStatus).toBe('DISABLED')

  // verify can't edit our details
  await expect(client.mutate({mutation: mutations.setUserDetails, variables: {bio: 'a dog'}})).rejects.toThrow(
    /ClientError: User .* is not ACTIVE/,
  )
})

test('setUserDetails without any arguments returns an error', async () => {
  const {client} = await loginCache.getCleanLogin()
  await expect(client.mutate({mutation: mutations.setUserDetails})).rejects.toThrow(
    /ClientError: Called without any arguments/,
  )
})

test('Try to get user that does not exist', async () => {
  const {client} = await loginCache.getCleanLogin()
  const userId = uuidv4()

  let resp = await client.query({query: queries.user, variables: {userId}})
  expect(resp.data.user).toBeNull()
})

test('Various photoPostId failures', async () => {
  const {client: ourClient} = await loginCache.getCleanLogin()
  const {client: theirClient} = await loginCache.getCleanLogin()

  // verify can't set profile photo using post that doesn't exist
  let postId = 'post-id-dne'
  await expect(
    ourClient.mutate({mutation: mutations.setUserDetails, variables: {photoPostId: 'post-id-dne'}}),
  ).rejects.toThrow(/ClientError: .*not found/)

  // create a text-only post
  postId = uuidv4()
  let variables = {postId, text: 'l', postType: 'TEXT_ONLY'}
  let resp = await ourClient.mutate({mutation: mutations.addPost, variables})
  expect(resp.data.addPost.postId).toBe(postId)
  expect(resp.data.addPost.postStatus).toBe('COMPLETED')
  expect(resp.data.addPost.postType).toBe('TEXT_ONLY')

  // verify can't set profile photo using text-only post
  await expect(
    ourClient.mutate({mutation: mutations.setUserDetails, variables: {photoPostId: postId}}),
  ).rejects.toThrow(/ClientError: .*does not have type/)

  // create an image post, leave it in pending
  postId = uuidv4()
  resp = await ourClient.mutate({mutation: mutations.addPost, variables: {postId, postType: 'IMAGE'}})
  expect(resp.data.addPost.postId).toBe(postId)
  expect(resp.data.addPost.postStatus).toBe('PENDING')
  expect(resp.data.addPost.postType).toBe('IMAGE')

  // verify can't set profile photo using pending image post
  variables = {photoPostId: postId}
  await expect(ourClient.mutate({mutation: mutations.setUserDetails, variables})).rejects.toThrow(
    /ClientError: .*does not have status/,
  )

  // the other user creates an image post
  postId = uuidv4()
  resp = await theirClient.mutate({mutation: mutations.addPost, variables: {postId, imageData: grantDataB64}})
  expect(resp.data.addPost.postId).toBe(postId)
  expect(resp.data.addPost.postStatus).toBe('COMPLETED')
  expect(resp.data.addPost.postType).toBe('IMAGE')

  // verify can't set our profile photo using their post
  variables = {photoPostId: postId}
  await expect(ourClient.mutate({mutation: mutations.setUserDetails, variables})).rejects.toThrow(
    /ClientError: .*does not belong to/,
  )

  // we create an image post that doesn't pass verification
  postId = uuidv4()
  resp = await ourClient.mutate({mutation: mutations.addPost, variables: {postId, imageData: grantDataB64}})
  expect(resp.data.addPost.postId).toBe(postId)
  expect(resp.data.addPost.postStatus).toBe('COMPLETED')
  expect(resp.data.addPost.postType).toBe('IMAGE')
})

test('Set and delete our profile photo, using postId', async () => {
  const {client} = await loginCache.getCleanLogin()

  // check that it's not already set
  let resp = await client.query({query: queries.self})
  expect(resp.data.self.photo).toBeNull()

  // create a post with an image that we can use as a profile pic
  const postId = uuidv4()
  let variables = {postId, imageData: grantDataB64, takenInReal: true}
  resp = await client.mutate({mutation: mutations.addPost, variables})
  expect(resp.data.addPost.postId).toBe(postId)
  expect(resp.data.addPost.postStatus).toBe('COMPLETED')
  expect(resp.data.addPost.postType).toBe('IMAGE')

  // set our photo
  resp = await client.mutate({mutation: mutations.setUserDetails, variables: {photoPostId: postId}})
  let image = resp.data.setUserDetails.photo
  expect(image.url).toBeTruthy()
  expect(image.url64p).toBeTruthy()
  expect(image.url480p).toBeTruthy()
  expect(image.url1080p).toBeTruthy()
  expect(image.url4k).toBeTruthy()

  // check that it is really set already set, and that root urls are same as before
  resp = await client.query({query: queries.self})
  expect(image.url.split('?')[0]).toBe(resp.data.self.photo.url.split('?')[0])
  expect(image.url64p.split('?')[0]).toBe(resp.data.self.photo.url64p.split('?')[0])
  expect(image.url480p.split('?')[0]).toBe(resp.data.self.photo.url480p.split('?')[0])
  expect(image.url1080p.split('?')[0]).toBe(resp.data.self.photo.url1080p.split('?')[0])
  expect(image.url4k.split('?')[0]).toBe(resp.data.self.photo.url4k.split('?')[0])

  // check we can access those urls
  await rp.head({uri: image.url, simple: true})
  await rp.head({uri: image.url4k, simple: true})
  await rp.head({uri: image.url1080p, simple: true})
  await rp.head({uri: image.url480p, simple: true})
  await rp.head({uri: image.url64p, simple: true})

  // delete our photo
  resp = await client.mutate({mutation: mutations.setUserDetails, variables: {photoPostId: ''}})
  expect(resp.data.setUserDetails.photo).toBeNull()

  // check that it really got deleted
  resp = await client.query({query: queries.self})
  expect(resp.data.self.photo).toBeNull()
})

test('Read properties of another private user', async () => {
  const {client: ourClient, userId: ourUserId} = await loginCache.getCleanLogin()

  // set up another user in cognito, mark them as private
  const theirBio = 'keeping calm and carrying on'
  const theirFullName = 'HG Wells'
  const theirPhone = '+15105551000'
  const theirGender = 'Male'
  const theirBirthDate = '1990-01-01'
  const {client: theirClient, userId: theirUserId, email: theirEmail} = await cognito.getAppSyncLogin(theirPhone)
  let variables = {privacyStatus: 'PRIVATE'}
  let resp = await theirClient.mutate({mutation: mutations.setUserPrivacyStatus, variables})
  expect(resp.data.setUserDetails.privacyStatus).toBe('PRIVATE')
  await theirClient.mutate({
    mutation: mutations.setUserDetails,
    variables: {bio: theirBio, fullName: theirFullName, gender: theirGender, birthDate: theirBirthDate},
  })

  // verify they can see all their properties (make sure they're all set correctly)
  resp = await theirClient.query({query: queries.self})
  let user = resp.data.self
  expect(user.followedStatus).toBe('SELF')
  expect(user.followerStatus).toBe('SELF')
  expect(user.privacyStatus).toBe('PRIVATE')
  expect(user.fullName).toBe(theirFullName)
  expect(user.bio).toBe(theirBio)
  expect(user.email).toBe(theirEmail)
  expect(user.phoneNumber).toBe(theirPhone)
  expect(user.gender).toBe(theirGender)
  expect(user.birthDate).toBe(theirBirthDate)

  // verify that we can only see info that is expected of a non-follower
  resp = await ourClient.query({query: queries.user, variables: {userId: theirUserId}})
  user = resp.data.user
  expect(user.followedStatus).toBe('NOT_FOLLOWING')
  expect(user.followerStatus).toBe('NOT_FOLLOWING')
  expect(user.privacyStatus).toBe('PRIVATE')
  expect(user.fullName).toBe(theirFullName)
  expect(user.bio).toBeNull()
  expect(user.email).toBeNull()
  expect(user.phoneNumber).toBeNull()
  expect(user.gender).toBeNull()
  expect(user.birthDate).toBeNull()

  // request to follow the user, verify we cannot see anything more
  await ourClient.mutate({mutation: mutations.followUser, variables: {userId: theirUserId}})
  resp = await ourClient.query({query: queries.user, variables: {userId: theirUserId}})
  user = resp.data.user
  expect(user.followedStatus).toBe('REQUESTED')
  expect(user.fullName).toBe(theirFullName)
  expect(user.bio).toBeNull()
  expect(user.email).toBeNull()
  expect(user.phoneNumber).toBeNull()
  expect(user.gender).toBeNull()
  expect(user.birthDate).toBeNull()

  // verify we see the same thing if we access their user profile indirectly
  resp = await ourClient.query({query: queries.ourFollowedUsers, variables: {followStatus: 'REQUESTED'}})
  expect(resp.data.self.followedUsers.items).toHaveLength(1)
  user = resp.data.self.followedUsers.items[0]
  expect(user.followedStatus).toBe('REQUESTED')
  expect(user.fullName).toBe(theirFullName)
  expect(user.bio).toBeNull()
  expect(user.email).toBeNull()
  expect(user.phoneNumber).toBeNull()
  expect(user.gender).toBeNull()
  expect(user.birthDate).toBeNull()

  // accept the user's follow request, verify we can see more
  await theirClient.mutate({mutation: mutations.acceptFollowerUser, variables: {userId: ourUserId}})
  resp = await ourClient.query({query: queries.user, variables: {userId: theirUserId}})
  user = resp.data.user
  expect(user.followedStatus).toBe('FOLLOWING')
  expect(user.fullName).toBe(theirFullName)
  expect(user.bio).toBe(theirBio)
  expect(user.gender).toBe(theirGender)
  expect(user.birthDate).toBe(theirBirthDate)
  expect(user.email).toBeNull()
  expect(user.phoneNumber).toBeNull()

  // verify we see the same thing if we access their user profile indirectly
  resp = await ourClient.query({query: queries.ourFollowedUsers})
  expect(resp.data.self.followedUsers.items).toHaveLength(1)
  user = resp.data.self.followedUsers.items[0]
  expect(user.followedStatus).toBe('FOLLOWING')
  expect(user.fullName).toBe(theirFullName)
  expect(user.bio).toBe(theirBio)
  expect(user.gender).toBe(theirGender)
  expect(user.birthDate).toBe(theirBirthDate)
  expect(user.email).toBeNull()
  expect(user.phoneNumber).toBeNull()

  // now deny the user's follow request, verify we can see less
  await theirClient.mutate({mutation: mutations.denyFollowerUser, variables: {userId: ourUserId}})
  resp = await ourClient.query({query: queries.user, variables: {userId: theirUserId}})
  user = resp.data.user
  expect(user.followedStatus).toBe('DENIED')
  expect(user.fullName).toBe(theirFullName)
  expect(user.bio).toBeNull()
  expect(user.gender).toBeNull()
  expect(user.birthDate).toBeNull()
  expect(user.email).toBeNull()
  expect(user.phoneNumber).toBeNull()

  // verify we see the same thing if we access their user profile indirectly
  resp = await ourClient.query({query: queries.ourFollowedUsers, variables: {followStatus: 'DENIED'}})
  expect(resp.data.self.followedUsers.items).toHaveLength(1)
  user = resp.data.self.followedUsers.items[0]
  expect(user.followedStatus).toBe('DENIED')
  expect(user.fullName).toBe(theirFullName)
  expect(user.bio).toBeNull()
  expect(user.gender).toBeNull()
  expect(user.birthDate).toBeNull()
  expect(user.email).toBeNull()
  expect(user.phoneNumber).toBeNull()

  // now accept the user's follow request, and then unfollow them
  await theirClient.mutate({mutation: mutations.acceptFollowerUser, variables: {userId: ourUserId}})
  await ourClient.mutate({mutation: mutations.unfollowUser, variables: {userId: theirUserId}})
  resp = await ourClient.query({query: queries.user, variables: {userId: theirUserId}})
  expect(resp.data.user.followedStatus).toBe('NOT_FOLLOWING')
  expect(resp.data.user.fullName).toBe(theirFullName)
  expect(resp.data.user.bio).toBeNull()
  expect(user.data.gender).toBeNull()
  expect(user.data.birthDate).toBeNull()
  expect(resp.data.user.email).toBeNull()
  expect(resp.data.user.phoneNumber).toBeNull()
})

test('Read properties of another public user', async () => {
  const {client: ourClient} = await loginCache.getCleanLogin()

  // set up another user in cognito, leave them as public
  const theirBio = 'keeping calm and carrying on'
  const theirFullName = 'HG Wells'
  const theirPhone = '+14155551212'
  const theirGender = 'Male'
  const theirBirthDate = '1990-01-01'
  const {client: theirClient, userId: theirUserId, email: theirEmail} = await cognito.getAppSyncLogin(theirPhone)
  await theirClient.mutate({
    mutation: mutations.setUserDetails,
    variables: {bio: theirBio, fullName: theirFullName, gender: theirGender, birthDate: theirBirthDate},
  })

  // verify they can see all their properties (make sure they're all set correctly)
  let resp = await theirClient.query({query: queries.self})
  let user = resp.data.self
  expect(user.followedStatus).toBe('SELF')
  expect(user.followerStatus).toBe('SELF')
  expect(user.privacyStatus).toBe('PUBLIC')
  expect(user.fullName).toBe(theirFullName)
  expect(user.bio).toBe(theirBio)
  expect(user.gender).toBe(theirGender)
  expect(user.birthDate).toBe(theirBirthDate)
  expect(user.email).toBe(theirEmail)
  expect(user.phoneNumber).toBe(theirPhone)

  // verify that we can see info that is expected of a non-follower
  resp = await ourClient.query({query: queries.user, variables: {userId: theirUserId}})
  user = resp.data.user
  expect(user.followedStatus).toBe('NOT_FOLLOWING')
  expect(user.followerStatus).toBe('NOT_FOLLOWING')
  expect(user.privacyStatus).toBe('PUBLIC')
  expect(user.bio).toBe(theirBio)
  expect(user.fullName).toBe(theirFullName)
  expect(user.gender).toBe(theirGender)
  expect(user.birthDate).toBe(theirBirthDate)
  expect(user.email).toBeNull()
  expect(user.phoneNumber).toBeNull()

  // follow the user, and verify we still can't see stuff we shouldn't be able to
  await ourClient.mutate({mutation: mutations.followUser, variables: {userId: theirUserId}})
  resp = await ourClient.query({query: queries.user, variables: {userId: theirUserId}})
  expect(resp.data.user.email).toBeNull()
  expect(resp.data.user.phoneNumber).toBeNull()

  // verify we can't see anything more if we access their user profile indirectly
  resp = await ourClient.query({query: queries.ourFollowedUsers})
  expect(resp.data.self.followedUsers.items).toHaveLength(1)
  user = resp.data.self.followedUsers.items[0]
  expect(user.followedStatus).toBe('FOLLOWING')
  expect(user.followerStatus).toBe('NOT_FOLLOWING')
  expect(user.privacyStatus).toBe('PUBLIC')
  expect(user.bio).toBe(theirBio)
  expect(user.fullName).toBe(theirFullName)
  expect(user.gender).toBe(theirGender)
  expect(user.birthDate).toBe(theirBirthDate)
  expect(user.email).toBeNull()
  expect(user.phoneNumber).toBeNull()
})

test('User language code - get, set, privacy', async () => {
  const {client, userId} = await loginCache.getCleanLogin()

  // we should default to english
  let resp = await client.query({query: queries.user, variables: {userId}})
  expect(resp.data.user.languageCode).toBe('en')

  // we change our language code
  resp = await client.mutate({mutation: mutations.setUserLanguageCode, variables: {languageCode: 'de'}})
  expect(resp.data.setUserDetails.languageCode).toBe('de')

  // check another user can't see our language
  const {client: theirClient} = await loginCache.getCleanLogin()
  resp = await theirClient.query({query: queries.user, variables: {userId}})
  expect(resp.data.user.languageCode).toBeNull()
})

test('User theme code - get, set, privacy', async () => {
  const {client, userId} = await loginCache.getCleanLogin()

  // we should default to 'black.green'
  let resp = await client.query({query: queries.user, variables: {userId}})
  expect(resp.data.user.themeCode).toBe('black.green')

  // we change our theme code
  resp = await client.mutate({mutation: mutations.setUserThemeCode, variables: {themeCode: 'green.orange'}})
  expect(resp.data.setUserDetails.themeCode).toBe('green.orange')

  // we go to private
  resp = await client.mutate({mutation: mutations.setUserPrivacyStatus, variables: {privacyStatus: 'PRIVATE'}})
  expect(resp.data.setUserDetails.privacyStatus).toBe('PRIVATE')

  // Check to ensure another rando *can* see our themeCode
  // This is necessary because profile pics are planned to have some styling based on chosen theme
  const {client: theirClient} = await loginCache.getCleanLogin()
  resp = await theirClient.query({query: queries.user, variables: {userId}})
  expect(resp.data.user.themeCode).toBe('green.orange')
})

test('User accepted EULA version - get, set, privacy', async () => {
  const {client, userId} = await loginCache.getCleanLogin()

  // we should default to null
  let resp = await client.query({query: queries.self})
  expect(resp.data.self.acceptedEULAVersion).toBeNull()

  // we change our accepted version
  resp = await client.mutate({
    mutation: mutations.setUserAcceptedEULAVersion,
    variables: {version: '2019-11-14'},
  })
  expect(resp.data.setUserAcceptedEULAVersion.acceptedEULAVersion).toBe('2019-11-14')

  // check to make sure that version stuck
  resp = await client.query({query: queries.self})
  expect(resp.data.self.acceptedEULAVersion).toBe('2019-11-14')

  // check another user can't see our acepted version
  const {client: theirClient} = await loginCache.getCleanLogin()
  resp = await theirClient.query({query: queries.user, variables: {userId: userId}})
  expect(resp.data.user.acceptedEULAVersion).toBeNull()

  // check we can null out accepted version
  resp = await client.mutate({mutation: mutations.setUserAcceptedEULAVersion, variables: {version: ''}})
  expect(resp.data.setUserAcceptedEULAVersion.acceptedEULAVersion).toBeNull()

  // disable ourselves
  resp = await client.mutate({mutation: mutations.disableUser})
  expect(resp.data.disableUser.userId).toBe(userId)
  expect(resp.data.disableUser.userStatus).toBe('DISABLED')

  // verify we can no longer edit the EULA
  await expect(
    client.mutate({mutation: mutations.setUserAcceptedEULAVersion, variables: {version: '42'}}),
  ).rejects.toThrow(/ClientError: User .* is not ACTIVE/)
})

test('User commentsDisabled - get, set, privacy', async () => {
  const {client, userId} = await loginCache.getCleanLogin()

  // we should default to false
  let resp = await client.query({query: queries.self})
  expect(resp.data.self.commentsDisabled).toBe(false)

  // we change it
  let variables = {commentsDisabled: true}
  resp = await client.mutate({mutation: mutations.setUserMentalHealthSettings, variables})
  expect(resp.data.setUserDetails.commentsDisabled).toBe(true)

  // check to make sure that version stuck
  resp = await client.query({query: queries.self})
  expect(resp.data.self.commentsDisabled).toBe(true)

  // check another user can't see values
  const {client: theirClient} = await loginCache.getCleanLogin()
  resp = await theirClient.query({query: queries.user, variables: {userId: userId}})
  expect(resp.data.user.commentsDisabled).toBeNull()
})

test('User likesDisabled - get, set, privacy', async () => {
  const {client: ourClient, userId: ourUserId} = await loginCache.getCleanLogin()
  const {client: theirClient} = await loginCache.getCleanLogin()

  // we should default to false
  await ourClient.query({query: queries.self}).then(({data}) => expect(data.self.likesDisabled).toBe(false))

  // we change it, verify that stuck
  await ourClient
    .mutate({mutation: mutations.setUserMentalHealthSettings, variables: {likesDisabled: true}})
    .then(({data}) => expect(data.setUserDetails.likesDisabled).toBe(true))
  await ourClient.query({query: queries.self}).then(({data}) => expect(data.self.likesDisabled).toBe(true))

  // check another user can't see our values
  await theirClient
    .query({query: queries.user, variables: {userId: ourUserId}})
    .then(({data}) => expect(data.user.likesDisabled).toBeNull())
})

test('User sharingDisabled - get, set, privacy', async () => {
  const {client, userId} = await loginCache.getCleanLogin()

  // we should default to false
  let resp = await client.query({query: queries.self})
  expect(resp.data.self.sharingDisabled).toBe(false)

  // we change it
  let variables = {sharingDisabled: true}
  resp = await client.mutate({mutation: mutations.setUserMentalHealthSettings, variables})
  expect(resp.data.setUserDetails.sharingDisabled).toBe(true)

  // check to make sure that version stuck
  resp = await client.query({query: queries.self})
  expect(resp.data.self.sharingDisabled).toBe(true)

  // check another user can't see values
  const {client: theirClient} = await loginCache.getCleanLogin()
  resp = await theirClient.query({query: queries.user, variables: {userId}})
  expect(resp.data.user.sharingDisabled).toBeNull()
})

test('User verificationHidden - get, set, privacy', async () => {
  const {client, userId} = await loginCache.getCleanLogin()

  // we should default to false
  let resp = await client.query({query: queries.self})
  expect(resp.data.self.verificationHidden).toBe(false)

  // we change it
  resp = await client.mutate({
    mutation: mutations.setUserMentalHealthSettings,
    variables: {verificationHidden: true},
  })
  expect(resp.data.setUserDetails.verificationHidden).toBe(true)

  // check to make sure that version stuck
  resp = await client.query({query: queries.self})
  expect(resp.data.self.verificationHidden).toBe(true)

  // check another user can't see values
  const {client: theirClient} = await loginCache.getCleanLogin()
  resp = await theirClient.query({query: queries.user, variables: {userId}})
  expect(resp.data.user.verificationHidden).toBeNull()
})

test('User setUserAPNSToken', async () => {
  const {client, userId} = await loginCache.getCleanLogin()

  // reading the APNS token is purposefully left out of the api, so verification here is limited

  // set it
  let resp = await client.mutate({mutation: mutations.setUserAPNSToken, variables: {token: 'apns-token'}})
  expect(resp.data.setUserAPNSToken.userId).toBe(userId)

  // change it
  resp = await client.mutate({mutation: mutations.setUserAPNSToken, variables: {token: 'apns-token-other'}})
  expect(resp.data.setUserAPNSToken.userId).toBe(userId)

  // delete it
  resp = await client.mutate({mutation: mutations.setUserAPNSToken, variables: {token: ''}})
  expect(resp.data.setUserAPNSToken.userId).toBe(userId)
})
